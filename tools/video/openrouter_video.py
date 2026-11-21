"""Video generation through OpenRouter's video API (openrouter.ai/api/v1/videos).

One OpenRouter key reaches several video model families (Google Veo, ByteDance
Seedance, MiniMax Hailuo, Alibaba Wan, xAI Grok Imagine, Runway and others)
behind a single asynchronous API: submit, poll ``/videos/{id}``, download from
the authenticated content endpoint.

The model catalog (durations, resolutions, aspect ratios, frame support, audio,
seed, passthrough parameters and ``pricing_skus``) is read live from
``GET /videos/models`` and falls back to the snapshot in
``tools/provider_contracts/openrouter_video_models.json``. Requests are
validated against it before any paid call, and the preflight estimate is
computed from the model's ``pricing_skus``. The completed job reports the
charged cost in ``usage.cost``, which this tool returns as ``cost_usd``.

Local frame and reference images are sent inline as base64 data URIs, so
nothing is uploaded to a third-party host first.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import requests

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ResumeSupport,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)
from tools.provider_jobs import save_job
from tools.provider_pricing import PriceQuoteRequired

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "provider_contracts" / "openrouter_video_models.json"
)
DEFAULT_MODEL = "google/veo-3.1-lite"
DEFAULT_DURATION = 5
DEFAULT_RESOLUTION = "720p"
DEFAULT_ASPECT_RATIO = "16:9"
CATALOG_TTL_SECONDS = 3600
# Seedance bills video tokens: width x height x frames / 1024, with frames = 24 x seconds + 1.
# The rendered frame can exceed the nominal tier (480p 16:9 renders at 864x496, verified
# 2026-11-21), so token estimates carry a margin to keep preflight from undercounting.
TOKEN_FPS = 24
TOKEN_ESTIMATE_MARGIN = 1.05
INLINE_IMAGE_WARN_BYTES = 10 * 1024 * 1024

OPERATIONS = ("text_to_video", "image_to_video", "reference_to_video")
IN_FLIGHT = {"pending", "in_progress"}
TERMINAL_FAILURES = {"failed", "cancelled", "expired"}

# Standard 16:9-family sizes used to turn a resolution tier into pixels for
# token-priced models when the catalog does not list explicit sizes.
_TIER_SHORT_EDGE = {"360p": 360, "480p": 480, "720p": 720, "768p": 768, "1080p": 1080,
                    "1k": 1080, "2k": 1440, "4k": 2160}

_STATUS_HINTS = {
    401: "Check OPENROUTER_API_KEY.",
    402: "The OpenRouter account is out of credits; top up at https://openrouter.ai/settings/credits.",
    403: "The key is not allowed to use this model, or the request was blocked by moderation.",
    408: "The request timed out at OpenRouter; check the job before submitting again.",
    429: "Rate limited; wait and submit again.",
    502: "The upstream provider failed; another submission is usually fine.",
    503: "No provider is available for this model with the requested settings.",
}
_NO_ENDPOINTS_HINT = (
    "OpenRouter lists the model but no endpoint serves it right now (or none matches the "
    "provider preferences, such as exclude_data_collection). Try later or pick another model."
)

_catalog_cache: dict[str, Any] = {"loaded_at": 0.0, "models": None, "source": None}


class OpenRouterAPIError(RuntimeError):
    """An HTTP- or job-level error returned by OpenRouter."""

    def __init__(self, message: str, *, status: int | None = None, code: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code


def _load_snapshot() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _tier(value: str) -> str:
    """Normalize a resolution tier to the lowercase form used in SKU names."""
    return str(value).strip().lower()


class OpenRouterVideo(BaseTool):
    name = "openrouter_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "openrouter"
    hosting_provider = "openrouter"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.API
    resume_support = ResumeSupport.FROM_CHECKPOINT

    dependencies = ["env:OPENROUTER_API_KEY"]
    install_instructions = (
        "Set OPENROUTER_API_KEY to an OpenRouter API key.\n"
        "  1. Sign in at https://openrouter.ai and open Settings > Keys.\n"
        "  2. Create a key and add credits under Settings > Credits (prepaid).\n"
        "  One key reaches every video model in OpenRouter's catalog.\n"
        "  Optional: OPENROUTER_BASE_URL overrides https://openrouter.ai/api/v1."
    )
    agent_skills = ["openrouter-video", "ai-video-gen"]

    capabilities = ["text_to_video", "image_to_video", "reference_to_video"]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "first_last_frame_to_video": True,
        "reference_to_video": True,
        "reference_image": True,
        "last_frame": True,
        "native_audio": True,
        "silent_output": True,
        "seed": True,
        "negative_prompt": True,
        "multi_model_catalog": True,
        "short_clips": True,
    }
    best_for = [
        "one prepaid key for Veo 3.1, Seedance 2.x, Hailuo, Wan, Grok Imagine and Runway",
        "low-cost image-to-video from a local keyframe (Veo 3.1 Lite from $0.03/s at 720p)",
        "character-consistent reference-to-video through Seedance",
        "comparing model families on the same brief without new credentials",
    ]
    not_good_for = [
        "offline generation",
        "Kling models (not in OpenRouter's video catalog)",
        "cancelling a submitted job (the API has no cancel endpoint)",
    ]
    fallback_tools = ["veo_video", "seedance_video", "kling_video", "minimax_fal_video"]

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=1500, network_required=True
    )
    # Paid submission without a request idempotency key: never retried automatically.
    retry_policy = RetryPolicy(max_retries=0)
    idempotency_key_fields = [
        "model", "operation", "prompt", "duration", "resolution", "aspect_ratio", "seed",
        "generate_audio", "negative_prompt", "reference_image_path", "reference_image_url",
        "last_image_path", "last_image_url", "reference_image_paths", "reference_image_urls",
        "provider_options", "provider_preferences", "exclude_data_collection", "resume_job",
    ]
    side_effects = [
        "submits a paid OpenRouter video generation job",
        "sends local frame and reference images inline as base64 data URIs",
        "writes an MP4 and an optional job checkpoint",
    ]
    user_visible_verification = [
        "Watch the clip: motion direction, subject fidelity to the keyframe, artifacts",
        "Confirm data.usage.cost against the preflight estimate",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "minLength": 1},
            "model": {
                "type": "string",
                "default": DEFAULT_MODEL,
                "description": (
                    "OpenRouter video model id, e.g. google/veo-3.1-lite, bytedance/seedance-2.0, "
                    "minimax/hailuo-3-max. Validated against the live catalog."
                ),
            },
            "operation": {
                "type": "string",
                "enum": list(OPERATIONS),
                "description": (
                    "Inferred when omitted: references -> reference_to_video, a frame -> "
                    "image_to_video, else text_to_video."
                ),
            },
            "duration": {
                "type": ["integer", "string"],
                "description": "Whole seconds; must be one of the model's supported durations.",
            },
            "resolution": {"type": "string", "description": "Resolution tier, e.g. 480p, 720p, 1080p."},
            "aspect_ratio": {"type": "string", "description": "e.g. 16:9, 9:16, 1:1; checked against the model."},
            "seed": {"type": "integer", "minimum": 0},
            "generate_audio": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Native audio for models that support it. Off by default because it costs more; "
                    "OpenRouter itself defaults it on."
                ),
            },
            "negative_prompt": {
                "type": "string",
                "description": "Sent through provider passthrough for models that accept one (Veo).",
            },
            # Frames. The selector-facing names are used on purpose: advertising image_url
            # would make video_selector upload local frames to fal first.
            "reference_image_path": {"type": "string", "description": "Local first frame, sent inline."},
            "reference_image_url": {"type": "string", "description": "First-frame image URL (https)."},
            "last_image_path": {"type": "string", "description": "Local last frame, sent inline."},
            "last_image_url": {"type": "string", "description": "Last-frame image URL (https)."},
            "reference_image_paths": {
                "type": "array", "items": {"type": "string"},
                "description": "Local reference images for reference_to_video, sent inline.",
            },
            "reference_image_urls": {
                "type": "array", "items": {"type": "string"},
                "description": "Reference image URLs for reference_to_video.",
            },
            "exclude_data_collection": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Route only to endpoints that do not train on request data "
                    "(provider.data_collection=deny). May leave fewer or no endpoints for a model."
                ),
            },
            "provider_preferences": {
                "type": "object",
                "description": "OpenRouter provider routing preferences (order, only, ignore), merged verbatim.",
            },
            "provider_options": {
                "type": "object",
                "description": (
                    "Per-provider passthrough options keyed by provider slug, sent as provider.options. "
                    "Allowed keys per model are listed in the catalog's allowed_passthrough_parameters."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Validate and estimate the cost without submitting anything.",
            },
            "output_path": {"type": "string"},
            "job_path": {"type": "string", "description": "Where to persist the job checkpoint for resume."},
            "resume_job": {"type": "object", "description": "Job metadata from a timed-out call; polls without resubmitting."},
            "poll_timeout": {"type": "number", "exclusiveMinimum": 0, "default": 900},
            "poll_interval": {"type": "number", "exclusiveMinimum": 0, "default": 10},
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "operation": {"type": "string"},
            "job_id": {"type": "string"},
            "output": {"type": "string"},
            "request": {"type": "object"},
            "usage": {"type": ["object", "null"]},
            "cost_status": {"type": "string", "enum": ["settled", "pending", "estimated"]},
            "estimated_cost_usd": {"type": ["number", "null"]},
            "catalog_source": {"type": "string"},
            "resume_job": {"type": "object"},
        },
    }

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    @staticmethod
    def base_url() -> str:
        return os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    def _catalog(self) -> tuple[dict[str, dict[str, Any]], str]:
        """Return the video model catalog keyed by id and where it came from."""
        now = time.time()
        if _catalog_cache["models"] is not None and now - _catalog_cache["loaded_at"] < CATALOG_TTL_SECONDS:
            return _catalog_cache["models"], _catalog_cache["source"]
        models: dict[str, dict[str, Any]] | None = None
        source = "snapshot"
        try:
            headers = {}
            if os.environ.get("OPENROUTER_API_KEY"):
                headers["Authorization"] = f"Bearer {os.environ['OPENROUTER_API_KEY']}"
            response = requests.get(f"{self.base_url()}/videos/models", headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json().get("data")
                if isinstance(data, list) and data:
                    models = {m["id"]: m for m in data if isinstance(m, dict) and m.get("id")}
                    source = "live"
        except Exception:  # offline, DNS, malformed body: fall back to the pinned snapshot
            models = None
        if not models:
            models = {m["id"]: m for m in _load_snapshot()["data"]}
            source = "snapshot"
        _catalog_cache.update(loaded_at=now, models=models, source=source)
        return models, source

    @staticmethod
    def _snapshot_models() -> dict[str, dict[str, Any]]:
        return {m["id"]: m for m in _load_snapshot()["data"]}

    def vouches_for_model(self, model: str) -> bool:
        """Let explicit-model routing pick this tool for any id in the snapshot."""
        return model in self._snapshot_models()

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        snapshot = _load_snapshot()
        info["hosting_provider"] = self.hosting_provider
        info["model_catalog"] = {
            m["id"]: {
                "name": m.get("name"),
                "supported_durations": m.get("supported_durations"),
                "supported_resolutions": m.get("supported_resolutions"),
                "supported_frame_images": m.get("supported_frame_images"),
                "generate_audio": m.get("generate_audio"),
                "pricing_skus": m.get("pricing_skus"),
                "verified_on": snapshot.get("verified_on"),
            }
            for m in snapshot["data"]
            if self._is_generation_model(m)
        }
        info["billing"] = {
            "model": "prepaid_credits",
            "prepaid": True,
            "free_tier": False,
            "charged_cost_field": "usage.cost",
            "dashboard": "https://openrouter.ai/activity",
        }
        return info

    @staticmethod
    def _is_generation_model(model: dict[str, Any]) -> bool:
        # Upscalers report an upscale factor; editors take no durations or frames.
        return not model.get("upscale_factor") and bool(model.get("supported_durations"))

    # ------------------------------------------------------------------
    # Estimates
    # ------------------------------------------------------------------

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        resume_job = inputs.get("resume_job")
        if isinstance(resume_job, dict) and resume_job.get("id"):
            return float(resume_job.get("estimated_cost_usd") or 0.0)
        operation, model, payload = self._build_request({**inputs, "dry_run": True})
        return self._estimate(model, payload)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        try:
            seconds = int(str(inputs.get("duration") or DEFAULT_DURATION).rstrip("s"))
        except ValueError:
            seconds = DEFAULT_DURATION
        return 45.0 + 10.0 * seconds

    def _estimate(self, model: dict[str, Any], payload: dict[str, Any]) -> float:
        skus = model.get("pricing_skus") or {}
        if not skus:
            raise PriceQuoteRequired(f"{model['id']} publishes no pricing_skus")
        seconds = payload.get("duration")
        if not seconds or seconds < 0:
            raise PriceQuoteRequired(f"{model['id']} chooses its own duration; the cost is known after the job")
        tier = _tier(payload.get("resolution") or "")
        audio = bool(payload.get("generate_audio"))

        per_second = self._per_second_rate(skus, tier, audio)
        if per_second is not None:
            cost = per_second * seconds
        else:
            token_rate = self._token_rate(skus, tier, audio)
            if token_rate is None:
                raise PriceQuoteRequired(
                    f"Unrecognized pricing_skus for {model['id']}: {', '.join(sorted(skus))}"
                )
            width, height = self._pixel_size(model, payload)
            frames = TOKEN_FPS * seconds + 1
            cost = token_rate * width * height * frames / 1024 * TOKEN_ESTIMATE_MARGIN

        frames = len(payload.get("frame_images") or [])
        references = len(payload.get("input_references") or [])
        if "cents_per_image_input" in skus:
            cost += float(skus["cents_per_image_input"]) / 100 * (frames + references)
        for key in ("reference_images", "reference_input_images"):
            if key in skus and references:
                cost += float(skus[key]) * references
                break
        return round(cost, 4)

    @staticmethod
    def _per_second_rate(skus: dict[str, str], tier: str, audio: bool) -> float | None:
        wanted = "with_audio" if audio else "without_audio"
        other = "without_audio" if audio else "with_audio"
        dollar_keys = [
            f"duration_seconds_{wanted}_{tier}", f"duration_seconds_{wanted}",
            f"duration_seconds_{tier}", "duration_seconds",
            # A model that only lists the other audio variant always produces it.
            f"duration_seconds_{other}_{tier}", f"duration_seconds_{other}",
            f"per-video-second-{tier}", "per-video-second",
        ]
        for key in dollar_keys:
            if key in skus:
                return float(skus[key])
        cent_keys = [
            f"cents_per_video_output_second_{tier}", f"cents_per_second_output_{tier}",
            "cents_per_second_output",
        ]
        for key in cent_keys:
            if key in skus:
                return float(skus[key]) / 100
        return None

    @staticmethod
    def _token_rate(skus: dict[str, str], tier: str, audio: bool) -> float | None:
        keys = [f"video_tokens_{tier}"]
        if not audio:
            keys.append("video_tokens_without_audio")
        keys.append("video_tokens")
        for key in keys:
            if key in skus:
                return float(skus[key])
        return None

    @staticmethod
    def _pixel_size(model: dict[str, Any], payload: dict[str, Any]) -> tuple[int, int]:
        tier = _tier(payload.get("resolution") or DEFAULT_RESOLUTION)
        ratio = payload.get("aspect_ratio") or DEFAULT_ASPECT_RATIO
        try:
            rw, rh = (float(x) for x in ratio.split(":"))
        except ValueError:
            rw, rh = 16.0, 9.0
        short = _TIER_SHORT_EDGE.get(tier, 720)
        for size in model.get("supported_sizes") or []:
            w, h = (int(x) for x in size.split("x"))
            if min(w, h) == short and abs(w / h - rw / rh) < 0.02:
                return w, h
        if rw >= rh:
            return round(short * rw / rh), short
        return short, round(short * rh / rw)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.time()
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key and not inputs.get("dry_run"):
            return ToolResult(success=False, error="OPENROUTER_API_KEY not set. " + self.install_instructions)
        headers = {"Authorization": f"Bearer {api_key}"}
        job = dict(inputs.get("resume_job") or {})

        try:
            if not job:
                operation, model, payload = self._build_request(inputs)
                try:
                    estimate: float | None = self._estimate(model, payload)
                except PriceQuoteRequired:
                    estimate = None
                _, source = self._catalog()
                if inputs.get("dry_run"):
                    return ToolResult(
                        success=True,
                        data={
                            "provider": self.provider,
                            "model": model["id"],
                            "operation": operation,
                            "dry_run": True,
                            "request": self._redact(payload),
                            "estimated_cost_usd": estimate,
                            "cost_status": "estimated",
                            "catalog_source": source,
                        },
                        cost_usd=0.0,
                        duration_seconds=round(time.time() - started, 2),
                        model=model["id"],
                    )
                submitted = self._request(
                    "POST", f"{self.base_url()}/videos",
                    headers={**headers, "Content-Type": "application/json"}, json=payload,
                )
                if not submitted.get("id"):
                    raise OpenRouterAPIError("Submission returned no job id")
                job = {
                    "id": submitted["id"],
                    "tool": self.name,
                    "model": model["id"],
                    "operation": operation,
                    "polling_url": submitted.get("polling_url"),
                    "request": self._redact(payload),
                    "estimated_cost_usd": estimate,
                    "catalog_source": source,
                    "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                save_job(inputs.get("job_path"), job)

            if job.get("tool") != self.name or not job.get("id"):
                raise ValueError("resume_job does not belong to openrouter_video")

            job_url = self._absolute(job.get("polling_url") or f"/videos/{quote(str(job['id']), safe='')}")
            result = self._poll(
                lambda: self._request("GET", job_url, headers=headers),
                timeout=float(inputs.get("poll_timeout", 900)),
                interval=float(inputs.get("poll_interval", 10)),
            )
            if result is None:
                return ToolResult(
                    success=False,
                    error=(
                        f"OpenRouter job {job['id']} is still running after poll_timeout. "
                        "Pass data.resume_job back to keep polling; do not resubmit."
                    ),
                    data={"resume_job": job, "job_id": job["id"], "retryable": True, "provider": self.provider},
                    cost_usd=0.0,
                    duration_seconds=round(time.time() - started, 2),
                    model=job.get("model"),
                )
            status = str(result.get("status", "")).lower()
            if status in TERMINAL_FAILURES:
                detail = str(result.get("error") or "no detail")
                hint = "Do not resubmit blindly; a new submission is a new charge."
                lowered = detail.lower()
                if "filtered" in lowered or "content policy" in lowered or "moderation" in lowered:
                    hint = (
                        "The provider's safety filter blocked the output; violent or graphic keyframes "
                        "trigger it even with a softened prompt. Change the keyframe or use another "
                        "model family (Seedance passed the same frame)."
                    )
                    status = "filtered"
                return self._failure(
                    f"OpenRouter job {job['id']} {result.get('status')}: {detail}. {hint}",
                    job=job, started=started, code=status,
                )

            output_path = Path(inputs.get("output_path") or f"openrouter_{job['id']}.mp4")
            artifact = self._download(result, headers, output_path)

            usage = result.get("usage") or {}
            charged = usage.get("cost")
            if charged is not None:
                cost_usd, cost_status = float(charged), "settled"
            else:
                cost_usd, cost_status = job.get("estimated_cost_usd"), "pending"
            request = job.get("request") or {}
            return ToolResult(
                success=True,
                data={
                    "provider": self.provider,
                    "model": job.get("model"),
                    "operation": job.get("operation"),
                    "job_id": job["id"],
                    "generation_id": result.get("generation_id"),
                    "output": artifact,
                    "format": "mp4",
                    "request": request,
                    "resolution": request.get("resolution"),
                    "duration_requested": request.get("duration"),
                    "aspect_ratio": request.get("aspect_ratio"),
                    "native_audio": bool(request.get("generate_audio")),
                    "usage": usage or None,
                    "cost_status": cost_status,
                    "estimated_cost_usd": job.get("estimated_cost_usd"),
                    "catalog_source": job.get("catalog_source"),
                    "resume_job": job,
                },
                artifacts=[artifact],
                cost_usd=cost_usd,
                duration_seconds=round(time.time() - started, 2),
                seed=request.get("seed"),
                model=job.get("model"),
            )
        except OpenRouterAPIError as exc:
            hint = _STATUS_HINTS.get(exc.status or 0, "")
            if "no endpoints found" in str(exc).lower():
                hint = _NO_ENDPOINTS_HINT
            return self._failure(
                f"OpenRouter API error ({exc.status}): {exc}. {hint}".strip(),
                job=job, started=started, code=exc.code,
                retryable=exc.status in {429, 502, 503} and not job,
            )
        except Exception as exc:  # validation, file, download and transport errors
            return self._failure(f"OpenRouter video generation failed: {exc}", job=job, started=started)

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------

    def _build_request(self, inputs: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        catalog, _ = self._catalog()
        model_id = str(inputs.get("model") or DEFAULT_MODEL)
        model = catalog.get(model_id)
        if model is None:
            raise ValueError(
                f"Unknown OpenRouter video model {model_id!r}. Known: {', '.join(sorted(catalog))}"
            )
        if not self._is_generation_model(model):
            raise ValueError(f"{model_id} is not a video generation model (upscaler or editor)")

        prompt = str(inputs.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        operation = self._resolve_operation(inputs)
        payload: dict[str, Any] = {"model": model_id, "prompt": prompt}

        duration = self._duration(model, inputs.get("duration"))
        if duration is not None:
            payload["duration"] = duration
        payload["resolution"] = self._choice(
            model, "supported_resolutions", inputs.get("resolution"), DEFAULT_RESOLUTION, "resolution", _tier
        )
        payload["aspect_ratio"] = self._choice(
            model, "supported_aspect_ratios", inputs.get("aspect_ratio"), DEFAULT_ASPECT_RATIO, "aspect_ratio", str
        )
        if payload["resolution"] is None:
            payload.pop("resolution")
        if payload["aspect_ratio"] is None:
            payload.pop("aspect_ratio")

        audio = bool(inputs.get("generate_audio", False))
        if model.get("generate_audio"):
            payload["generate_audio"] = audio
        elif audio:
            raise ValueError(f"{model_id} does not generate audio")

        if inputs.get("seed") is not None:
            if not model.get("seed"):
                raise ValueError(f"{model_id} does not accept a seed")
            payload["seed"] = int(inputs["seed"])

        inline = not inputs.get("dry_run")
        if operation == "image_to_video":
            frames = []
            first = self._image(inputs.get("reference_image_url") or inputs.get("image_url"),
                                inputs.get("reference_image_path") or inputs.get("image_path"),
                                "first frame", inline)
            last = self._image(inputs.get("last_image_url"), inputs.get("last_image_path"), "last frame", inline)
            supported = set(model.get("supported_frame_images") or [])
            if first:
                if "first_frame" not in supported:
                    raise ValueError(f"{model_id} does not accept a first frame")
                frames.append({"type": "image_url", "image_url": {"url": first}, "frame_type": "first_frame"})
            if last:
                if "last_frame" not in supported:
                    raise ValueError(f"{model_id} does not accept a last frame")
                frames.append({"type": "image_url", "image_url": {"url": last}, "frame_type": "last_frame"})
            if not frames:
                raise ValueError("image_to_video needs reference_image_path/url or last_image_path/url")
            payload["frame_images"] = frames
        elif operation == "reference_to_video":
            refs = [self._image(url, None, "reference image", inline) for url in inputs.get("reference_image_urls") or []]
            refs += [self._image(None, path, "reference image", inline) for path in inputs.get("reference_image_paths") or []]
            if not refs:
                raise ValueError("reference_to_video needs reference_image_paths or reference_image_urls")
            payload["input_references"] = [{"type": "image_url", "image_url": {"url": r}} for r in refs]
        elif self._frame_inputs(inputs):
            raise ValueError("text_to_video does not take frames; use image_to_video")

        provider = self._provider_block(model, inputs)
        if provider:
            payload["provider"] = provider
        return operation, model, payload

    def _resolve_operation(self, inputs: dict[str, Any]) -> str:
        operation = inputs.get("operation")
        if operation in (None, "", "generate"):
            if inputs.get("reference_image_paths") or inputs.get("reference_image_urls"):
                return "reference_to_video"
            if self._frame_inputs(inputs):
                return "image_to_video"
            return "text_to_video"
        if operation not in OPERATIONS:
            raise ValueError(f"operation must be one of: {', '.join(OPERATIONS)}")
        return str(operation)

    @staticmethod
    def _frame_inputs(inputs: dict[str, Any]) -> bool:
        return any(inputs.get(k) for k in (
            "reference_image_path", "reference_image_url", "image_path", "image_url",
            "last_image_path", "last_image_url",
        ))

    @staticmethod
    def _duration(model: dict[str, Any], raw: Any) -> int | None:
        supported = [int(d) for d in model.get("supported_durations") or []]
        if raw is None or raw == "":
            if -1 in supported and DEFAULT_DURATION not in supported:
                return None  # the model picks its own length
            positive = sorted(d for d in supported if d > 0)
            if not positive:
                return None
            at_least = [d for d in positive if d >= DEFAULT_DURATION]
            return at_least[0] if at_least else positive[-1]
        text = str(raw).strip().lower().rstrip("s")
        try:
            value = int(float(text))
        except ValueError as exc:
            raise ValueError(f"duration must be a whole number of seconds, got {raw!r}") from exc
        if value != float(text):
            raise ValueError("duration must be a whole number of seconds")
        if supported and value not in supported:
            listed = ", ".join(str(d) for d in sorted(d for d in supported if d > 0))
            raise ValueError(f"{model['id']} supports durations {listed} s, not {value}")
        return value

    @staticmethod
    def _choice(model: dict[str, Any], field: str, raw: Any, default: str, label: str, norm) -> str | None:
        supported = model.get(field) or []
        lookup = {norm(v): v for v in supported}
        if raw:
            key = norm(raw)
            if supported and key not in lookup:
                raise ValueError(f"{model['id']} supports {label} {', '.join(supported)}, not {raw}")
            return lookup.get(key, str(raw))
        if not supported:
            return None
        return lookup.get(norm(default), supported[0])

    def _provider_block(self, model: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        provider: dict[str, Any] = {}
        prefs = inputs.get("provider_preferences") or {}
        if not isinstance(prefs, dict):
            raise ValueError("provider_preferences must be an object")
        provider.update(prefs)
        if inputs.get("exclude_data_collection"):
            provider["data_collection"] = "deny"
        options = inputs.get("provider_options") or {}
        if not isinstance(options, dict):
            raise ValueError("provider_options must be an object keyed by provider slug")
        options = json.loads(json.dumps(options))  # never mutate the caller's dict
        negative = inputs.get("negative_prompt")
        if negative:
            allowed = model.get("allowed_passthrough_parameters") or []
            if "negativePrompt" in allowed:
                # Veo nests its passthrough under the google-vertex provider's parameters.
                params = options.setdefault("google-vertex", {}).setdefault("parameters", {})
                params["negativePrompt"] = str(negative)
            elif "negative_prompt" in allowed:
                raise ValueError(
                    f"{model['id']} takes negative_prompt through provider_options keyed by its provider slug"
                )
            else:
                raise ValueError(f"{model['id']} does not accept a negative prompt")
        if options:
            provider["options"] = options
        return provider

    @staticmethod
    def _image(url: Any, path: Any, label: str, inline: bool) -> str | None:
        if url and path:
            raise ValueError(f"Specify the {label} as a URL or a local path, not both")
        if url:
            url = str(url)
            if not url.startswith("https://"):
                raise ValueError(f"{label} URL must be https; pass local files via the *_path input")
            return url
        if not path:
            return None
        file = Path(str(path))
        if not file.is_file():
            raise FileNotFoundError(f"{label} not found: {file}")
        mime = mimetypes.guess_type(file.name)[0] or ""
        if not mime.startswith("image/"):
            raise ValueError(f"{label} must be an image file, got {file.name}")
        if not inline:
            return f"data:{mime};base64,<{file.stat().st_size} bytes from {file.name}>"
        return f"data:{mime};base64," + base64.b64encode(file.read_bytes()).decode("ascii")

    @staticmethod
    def _redact(payload: dict[str, Any]) -> dict[str, Any]:
        """Copy of the request without inline image bytes, for checkpoints and results."""
        def scrub(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: scrub(v) for k, v in value.items()}
            if isinstance(value, list):
                return [scrub(v) for v in value]
            if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
                head, body = value.split(",", 1)
                if body.startswith("<"):
                    return value
                return f"{head},<{len(body) * 3 // 4} bytes>"
            return value
        return scrub(payload)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _absolute(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("/api/"):
            origin = self.base_url().split("/api/", 1)[0]
            return origin + url
        return urljoin(self.base_url() + "/", url.lstrip("/"))

    @staticmethod
    def _request(method: str, url: str, *, headers: dict[str, str] | None = None, **kwargs: Any) -> dict[str, Any]:
        response = requests.request(method, url, headers=headers, timeout=120, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400:
            message, code = None, None
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                message = body["error"].get("message")
                code = body["error"].get("code")
            raise OpenRouterAPIError(
                str(message or response.text or response.reason or "request failed"),
                status=response.status_code, code=code,
            )
        if not isinstance(body, dict):
            raise OpenRouterAPIError("Unexpected non-JSON response from OpenRouter", status=response.status_code)
        return body

    @staticmethod
    def _poll(fetch, *, timeout: float, interval: float) -> dict[str, Any] | None:
        """Poll until a terminal status. Returns None on timeout."""
        if timeout <= 0 or interval <= 0:
            raise ValueError("poll_timeout and poll_interval must be positive")
        deadline = time.monotonic() + timeout
        while True:
            result = fetch()
            status = str(result.get("status", "")).lower()
            if status not in IN_FLIGHT:
                return result
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    def _download(self, result: dict[str, Any], headers: dict[str, str], path: Path) -> str:
        """Fetch the first output, from the signed URL if present, else the authenticated content endpoint."""
        signed = result.get("urls") or []
        unsigned = result.get("unsigned_urls") or []
        if signed:
            url, auth = str(signed[0]), None
        elif unsigned:
            url, auth = self._absolute(str(unsigned[0])), headers
        else:
            raise ValueError(f"Completed job {result.get('id')} returned no output URL")
        if not url.startswith("https://"):
            raise ValueError("Video output must be an HTTPS URL")
        response = requests.get(url, headers=auth, timeout=300)
        if response.status_code >= 400:
            raise OpenRouterAPIError(f"Download failed with HTTP {response.status_code}", status=response.status_code)
        content = response.content
        if not content:
            raise ValueError("OpenRouter returned an empty video")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".part")
        temp.write_bytes(content)
        temp.replace(path)
        return str(path)

    def _failure(self, message: str, *, job: dict[str, Any], started: float, code: Any = None,
                 retryable: bool = False) -> ToolResult:
        data: dict[str, Any] = {"provider": self.provider, "retryable": retryable}
        if code:
            data["error_code"] = code
        if job:
            data["job_id"] = job.get("id")
            data["resume_job"] = job
        return ToolResult(
            success=False,
            error=message,
            data=data,
            cost_usd=0.0,
            duration_seconds=round(time.time() - started, 2),
            model=job.get("model") if job else None,
        )
