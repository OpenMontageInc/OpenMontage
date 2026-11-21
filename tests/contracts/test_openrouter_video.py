"""Contract tests for the OpenRouter video provider.

Every HTTP call is faked; the suite's network guard would block a real one.
The model catalog comes from the pinned snapshot in
tools/provider_contracts/openrouter_video_models.json.

Run: pytest tests/contracts/test_openrouter_video.py -v
"""

from __future__ import annotations

import base64
import json
from collections import deque
from pathlib import Path

import pytest

from tools.base_tool import BaseTool, ResumeSupport, ToolRuntime, ToolStatus, ToolTier
from tools.provider_pricing import PriceQuoteRequired
from tools.video import openrouter_video as module
from tools.video.openrouter_video import CATALOG_PATH, OpenRouterVideo

BASE = "https://openrouter.ai/api/v1"
JOB_ID = "gen-vid-1795274596-jOUKn1Dqlq1UzQV5YcQC"
CONTENT = f"/api/v1/videos/{JOB_ID}/content?index=0"
SNAPSHOT = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
MP4 = b"\x00\x00\x00\x18ftypmp42"


# ------------------------------------------------------------------
# Fake HTTP
# ------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", reason="OK"):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.reason = reason
        self.text = json.dumps(json_data) if json_data is not None else ""
        self.headers = {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeRequests:
    """Routes (method, url-substring) to queued responses and records every call."""

    def __init__(self):
        self.calls: list[dict] = []
        self._routes: list[tuple[str, str, deque]] = []

    def add(self, method, contains, *responses):
        wrapped = [r if isinstance(r, FakeResponse) else FakeResponse(200, r) for r in responses]
        self._routes.append((method.upper(), contains, deque(wrapped)))

    def _respond(self, method, url, **kwargs):
        self.calls.append({"method": method.upper(), "url": url, **kwargs})
        for route_method, contains, queue in self._routes:
            if route_method == method.upper() and contains in url:
                if not queue:
                    raise AssertionError(f"No more fake responses for {method} {url}")
                return queue.popleft() if len(queue) > 1 else queue[0]
        raise AssertionError(f"Unexpected request {method} {url}")

    def request(self, method, url, headers=None, timeout=None, json=None, **_):
        return self._respond(method, url, headers=headers, json=json)

    def get(self, url, headers=None, timeout=None, **_):
        return self._respond("GET", url, headers=headers, json=None)

    def by(self, method, contains):
        return [c for c in self.calls if c["method"] == method.upper() and contains in c["url"]]


def job(status, *, usage=0.12, error=None, signed=False, relative=False):
    body = {"id": JOB_ID, "generation_id": JOB_ID, "status": status,
            "polling_url": f"{BASE}/videos/{JOB_ID}"}
    if status == "completed":
        body["unsigned_urls"] = [CONTENT if relative else f"https://openrouter.ai{CONTENT}"]
        if signed:
            body["urls"] = ["https://storage.example.com/signed/video.mp4?sig=abc"]
            body["expires_at"] = 1795361000
        body["usage"] = {"cost": usage, "is_byok": False} if usage is not None else None
    if status in {"failed", "cancelled", "expired"}:
        body["error"] = error or "provider rejected the request"
    return body


@pytest.fixture(autouse=True)
def reset_catalog_cache():
    module._catalog_cache.update(loaded_at=0.0, models=None, source=None)
    yield
    module._catalog_cache.update(loaded_at=0.0, models=None, source=None)


@pytest.fixture()
def fake_http(monkeypatch):
    fake = FakeRequests()
    # The live catalog answers with the snapshot unless a test overrides it.
    fake.add("GET", "/videos/models", {"data": SNAPSHOT["data"]})
    monkeypatch.setattr(module, "requests", fake)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    return fake


@pytest.fixture()
def key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)


@pytest.fixture()
def frame(tmp_path):
    path = tmp_path / "frame.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    return path


def happy_path(fake, polls=None, *, video=MP4):
    # The content route goes first: its URL also contains /videos/{JOB_ID}.
    fake.add("GET", "/content", FakeResponse(200, content=video))
    fake.add("POST", "/videos", FakeResponse(202, {"id": JOB_ID, "polling_url": f"{BASE}/videos/{JOB_ID}",
                                                   "status": "pending"}))
    fake.add("GET", f"/videos/{JOB_ID}", *(polls or [job("pending"), job("in_progress"), job("completed")]))


def submitted_body(fake):
    posts = fake.by("POST", "/videos")
    assert len(posts) == 1
    return posts[0]["json"]


# ------------------------------------------------------------------
# Contract
# ------------------------------------------------------------------

class TestContract:
    def test_identity(self):
        tool = OpenRouterVideo()
        assert isinstance(tool, BaseTool)
        assert tool.name == "openrouter_video"
        assert tool.provider == "openrouter"
        assert tool.capability == "video_generation"
        assert tool.tier == ToolTier.GENERATE
        assert tool.runtime == ToolRuntime.API
        assert tool.resume_support == ResumeSupport.FROM_CHECKPOINT
        assert tool.dependencies == ["env:OPENROUTER_API_KEY"]

    def test_paid_submission_is_never_retried(self):
        assert OpenRouterVideo().retry_policy.max_retries == 0

    def test_does_not_advertise_image_url(self):
        # video_selector uploads local frames to fal for tools that advertise image_url.
        props = OpenRouterVideo.input_schema["properties"]
        assert "image_url" not in props
        assert "reference_image_path" in props

    def test_status_follows_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert OpenRouterVideo().get_status() == ToolStatus.UNAVAILABLE
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert OpenRouterVideo().get_status() == ToolStatus.AVAILABLE

    def test_model_catalog_excludes_upscalers_and_avatars(self):
        catalog = OpenRouterVideo().get_info()["model_catalog"]
        assert "google/veo-3.1-lite" in catalog
        assert "black-forest-labs/flux-video-upscale" not in catalog
        assert "heygen/avatar-iv" not in catalog

    def test_registry_discovers_it(self):
        from tools.tool_registry import registry
        registry.discover()
        assert "openrouter_video" in registry._tools

    def test_missing_key_fails_without_network(self, monkeypatch, fake_http):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = OpenRouterVideo().execute({"prompt": "a lighthouse"})
        assert not result.success
        assert "OPENROUTER_API_KEY" in result.error
        assert fake_http.calls == []


# ------------------------------------------------------------------
# Catalog
# ------------------------------------------------------------------

class TestCatalog:
    def test_live_catalog_is_used(self, key, fake_http):
        result = OpenRouterVideo().execute({"prompt": "x", "dry_run": True})
        assert result.data["catalog_source"] == "live"

    def test_snapshot_fallback_when_catalog_unreachable(self, key, monkeypatch):
        fake = FakeRequests()
        fake.add("GET", "/videos/models", FakeResponse(503, {"error": {"message": "down"}}))
        monkeypatch.setattr(module, "requests", fake)
        result = OpenRouterVideo().execute({"prompt": "x", "dry_run": True})
        assert result.success
        assert result.data["catalog_source"] == "snapshot"

    def test_unknown_model_rejected(self, key, fake_http):
        result = OpenRouterVideo().execute({"prompt": "x", "model": "kwaivgi/kling-v2.1", "dry_run": True})
        assert not result.success
        assert "Unknown OpenRouter video model" in result.error

    def test_upscaler_rejected(self, key, fake_http):
        result = OpenRouterVideo().execute(
            {"prompt": "x", "model": "black-forest-labs/flux-video-upscale", "dry_run": True}
        )
        assert not result.success
        assert "not a video generation model" in result.error

    def test_vouches_for_snapshot_models(self):
        tool = OpenRouterVideo()
        assert tool.vouches_for_model("bytedance/seedance-2.0")
        assert not tool.vouches_for_model("kwaivgi/kling-v2.1")


# ------------------------------------------------------------------
# Request building
# ------------------------------------------------------------------

class TestRequest:
    def dry(self, **inputs):
        return OpenRouterVideo().execute({"prompt": "riders on a ridge", "dry_run": True, **inputs})

    def test_defaults(self, key, fake_http):
        request = self.dry().data["request"]
        assert request["model"] == "google/veo-3.1-lite"
        # 5 s is not a Veo duration: the default picks the next supported one.
        assert request["duration"] == 6
        assert request["resolution"] == "720p"
        assert request["aspect_ratio"] == "16:9"
        assert request["generate_audio"] is False
        assert "provider" not in request

    def test_unsupported_duration_lists_choices(self, key, fake_http):
        result = self.dry(duration=5)
        assert not result.success
        assert "4, 6, 8" in result.error

    def test_duration_accepts_suffix(self, key, fake_http):
        assert self.dry(duration="8s").data["request"]["duration"] == 8

    def test_unsupported_resolution_and_ratio(self, key, fake_http):
        assert not self.dry(resolution="480p").success
        assert not self.dry(aspect_ratio="1:1").success

    def test_resolution_case_follows_catalog(self, key, fake_http):
        assert self.dry(model="google/veo-3.1", resolution="4k").data["request"]["resolution"] == "4K"

    def test_audio_on_request(self, key, fake_http):
        assert self.dry(generate_audio=True).data["request"]["generate_audio"] is True

    def test_audio_rejected_for_silent_model(self, key, fake_http):
        result = self.dry(model="minimax/hailuo-3-max", generate_audio=True)
        assert not result.success
        assert "does not generate audio" in result.error

    def test_silent_model_omits_audio_flag(self, key, fake_http):
        request = self.dry(model="minimax/hailuo-3-max").data["request"]
        assert "generate_audio" not in request

    def test_seed(self, key, fake_http):
        assert self.dry(seed=7).data["request"]["seed"] == 7
        assert not self.dry(model="minimax/hailuo-3-max", seed=7).success

    def test_model_chosen_duration_is_omitted(self, key, fake_http):
        request = self.dry(model="bytedance/seedance-2.5").data["request"]
        assert request["duration"] == 5  # 5 is listed, so it is used
        model = {"id": "m", "supported_durations": [-1, 4, 6]}
        assert OpenRouterVideo._duration(model, None) is None

    def test_first_frame_inline_and_redacted(self, key, fake_http, frame):
        result = self.dry(reference_image_path=str(frame), duration=4)
        assert result.data["operation"] == "image_to_video"
        item = result.data["request"]["frame_images"][0]
        assert item["frame_type"] == "first_frame"
        assert item["image_url"]["url"].startswith("data:image/jpeg;base64,<")

    def test_first_and_last_frame(self, key, fake_http, frame):
        frames = self.dry(reference_image_path=str(frame), last_image_path=str(frame), duration=4).data[
            "request"]["frame_images"]
        assert [f["frame_type"] for f in frames] == ["first_frame", "last_frame"]

    def test_last_frame_rejected_when_unsupported(self, key, fake_http, frame):
        result = self.dry(model="runway/gen-4.5", reference_image_path=str(frame),
                          last_image_path=str(frame), duration=5)
        assert not result.success
        assert "last frame" in result.error

    def test_frame_url_must_be_https(self, key, fake_http):
        result = self.dry(reference_image_url="http://example.com/a.jpg", duration=4)
        assert not result.success
        assert "https" in result.error

    def test_url_and_path_conflict(self, key, fake_http, frame):
        result = self.dry(reference_image_url="https://example.com/a.jpg", reference_image_path=str(frame))
        assert not result.success

    def test_missing_frame_file(self, key, fake_http, tmp_path):
        result = self.dry(reference_image_path=str(tmp_path / "missing.jpg"), duration=4)
        assert not result.success
        assert "not found" in result.error

    def test_text_to_video_rejects_frames(self, key, fake_http, frame):
        result = self.dry(operation="text_to_video", reference_image_path=str(frame))
        assert not result.success

    def test_references(self, key, fake_http, frame):
        result = self.dry(model="bytedance/seedance-2.0", reference_image_paths=[str(frame), str(frame)],
                          reference_image_urls=["https://example.com/face.png"], duration=5)
        assert result.data["operation"] == "reference_to_video"
        refs = result.data["request"]["input_references"]
        assert len(refs) == 3
        assert refs[0]["image_url"]["url"] == "https://example.com/face.png"

    def test_exclude_data_collection(self, key, fake_http):
        request = self.dry(exclude_data_collection=True,
                           provider_preferences={"order": ["google-vertex"]}).data["request"]
        assert request["provider"] == {"order": ["google-vertex"], "data_collection": "deny"}

    def test_negative_prompt_for_veo(self, key, fake_http):
        options = {"google-vertex": {"parameters": {"enhancePrompt": False}}}
        request = self.dry(negative_prompt="blurry", provider_options=options).data["request"]
        assert request["provider"]["options"]["google-vertex"]["parameters"] == {
            "enhancePrompt": False, "negativePrompt": "blurry"}
        assert options == {"google-vertex": {"parameters": {"enhancePrompt": False}}}

    def test_negative_prompt_rejected_elsewhere(self, key, fake_http):
        result = self.dry(model="minimax/hailuo-3-max", negative_prompt="blurry")
        assert not result.success


# ------------------------------------------------------------------
# Cost
# ------------------------------------------------------------------

class TestCost:
    def estimate(self, **inputs):
        return OpenRouterVideo().estimate_cost({"prompt": "x", **inputs})

    def test_veo_lite_matches_verified_charge(self, key, fake_http, frame):
        # Verified live: 4 s, 720p, no audio, first frame -> usage.cost 0.12.
        assert self.estimate(duration=4, reference_image_path=str(frame)) == 0.12

    def test_audio_and_default_tier(self, key, fake_http):
        assert self.estimate(duration=4, resolution="1080p", generate_audio=True) == 0.32
        assert self.estimate(duration=4, resolution="1080p") == 0.2

    def test_cents_per_second_and_image_input(self, key, fake_http, frame):
        cost = self.estimate(model="x-ai/grok-imagine-video-1.5-lite", duration=5, resolution="720p",
                             reference_image_path=str(frame))
        assert cost == pytest.approx(5 * 0.03 + 0.01)

    def test_seedance_tokens_do_not_undercount(self, key, fake_http):
        # Verified live: 4 s at 480p rendered 864x496 x 97 frames and settled at $0.142079.
        cost = self.estimate(model="bytedance/seedance-2.0-mini", duration=4, resolution="480p")
        assert cost == pytest.approx(0.0000035 * 854 * 480 * 97 / 1024 * 1.05, rel=1e-3)
        assert cost >= 0.142079

    def test_reference_image_surcharge(self, key, fake_http, frame):
        cost = self.estimate(model="minimax/hailuo-3", duration=5, reference_image_paths=[str(frame)])
        assert cost == pytest.approx(5 * 0.13 + 0.04)

    def test_unknown_sku_needs_quote(self, key, fake_http, monkeypatch):
        models = {m["id"]: dict(m) for m in SNAPSHOT["data"]}
        models["google/veo-3.1-lite"]["pricing_skus"] = {"mystery_unit": "1"}
        module._catalog_cache.update(loaded_at=module.time.time(), models=models, source="live")
        with pytest.raises(PriceQuoteRequired):
            self.estimate(duration=4)

    def test_resume_uses_saved_estimate(self):
        assert OpenRouterVideo().estimate_cost({"resume_job": {"id": JOB_ID, "estimated_cost_usd": 0.18}}) == 0.18


# ------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------

class TestExecute:
    def run(self, tmp_path, **inputs):
        return OpenRouterVideo().execute(
            {"prompt": "riders on a ridge", "duration": 4, "output_path": str(tmp_path / "out.mp4"), **inputs}
        )

    def test_happy_path(self, key, fake_http, tmp_path, frame):
        happy_path(fake_http)
        result = self.run(tmp_path, reference_image_path=str(frame))
        assert result.success, result.error
        assert Path(result.data["output"]).read_bytes() == MP4
        assert result.cost_usd == 0.12
        assert result.data["cost_status"] == "settled"
        body = submitted_body(fake_http)
        url = body["frame_images"][0]["image_url"]["url"]
        assert base64.b64decode(url.split(",", 1)[1]) == frame.read_bytes()
        assert body["generate_audio"] is False
        # Content endpoint needs the key; results never carry inline image bytes.
        download = fake_http.by("GET", "/content")[0]
        assert download["headers"]["Authorization"] == "Bearer sk-or-test"
        assert "<" in result.data["request"]["frame_images"][0]["image_url"]["url"]

    def test_relative_urls(self, key, fake_http, tmp_path):
        fake_http.add("GET", "/content", FakeResponse(200, content=MP4))
        fake_http.add("POST", "/videos", FakeResponse(202, {"id": JOB_ID, "polling_url": f"/api/v1/videos/{JOB_ID}",
                                                             "status": "pending"}))
        fake_http.add("GET", f"/videos/{JOB_ID}", job("completed", relative=True))
        result = self.run(tmp_path)
        assert result.success, result.error
        assert fake_http.by("GET", "/content")[0]["url"] == f"https://openrouter.ai{CONTENT}"

    def test_signed_url_downloads_without_key(self, key, fake_http, tmp_path):
        fake_http.add("POST", "/videos", FakeResponse(202, {"id": JOB_ID, "status": "pending"}))
        fake_http.add("GET", f"/videos/{JOB_ID}", job("completed", signed=True))
        fake_http.add("GET", "storage.example.com", FakeResponse(200, content=MP4))
        result = self.run(tmp_path)
        assert result.success, result.error
        assert fake_http.by("GET", "storage.example.com")[0]["headers"] is None

    def test_missing_usage_reports_estimate(self, key, fake_http, tmp_path):
        happy_path(fake_http, [job("completed", usage=None)])
        result = self.run(tmp_path)
        assert result.success
        assert result.data["cost_status"] == "pending"
        assert result.cost_usd == 0.12

    @pytest.mark.parametrize("status", ["failed", "cancelled", "expired"])
    def test_terminal_failure_is_not_resubmitted(self, key, fake_http, tmp_path, status):
        happy_path(fake_http, [job("in_progress"), job(status, error="content policy")])
        result = self.run(tmp_path)
        assert not result.success
        assert status in result.error and "content policy" in result.error
        assert result.data["resume_job"]["id"] == JOB_ID
        assert len(fake_http.by("POST", "/videos")) == 1

    def test_filtered_output_gets_a_specific_hint(self, key, fake_http, tmp_path):
        filtered = "Video generation completed with no output (content may have been filtered)"
        happy_path(fake_http, [job("failed", error=filtered)])
        result = self.run(tmp_path)
        assert not result.success
        assert result.data["error_code"] == "filtered"
        assert "safety filter" in result.error
        assert len(fake_http.by("POST", "/videos")) == 1

    def test_policy_block_counts_as_filtered(self, key, fake_http, tmp_path):
        happy_path(fake_http, [job("failed", error="the prompt was blocked by the content policy")])
        result = self.run(tmp_path)
        assert result.data["error_code"] == "filtered"

    def test_no_endpoints_hint(self, key, fake_http, tmp_path):
        fake_http.add("POST", "/videos", FakeResponse(404, {"error": {"code": 404,
                      "message": "No endpoints found for minimax/hailuo-3-max."}}))
        result = self.run(tmp_path)
        assert not result.success
        assert "no endpoint serves it right now" in result.error
        assert result.data["retryable"] is False

    def test_timeout_returns_resume_job_then_resumes(self, key, fake_http, tmp_path):
        happy_path(fake_http, [job("in_progress")])
        result = self.run(tmp_path, poll_timeout=0.001, poll_interval=0.001)
        assert not result.success
        assert result.data["retryable"] is True
        resume = result.data["resume_job"]
        assert "base64," not in json.dumps(resume) or "<" in json.dumps(resume)

        fake_http._routes = [r for r in fake_http._routes if r[1] != f"/videos/{JOB_ID}"]
        fake_http.add("GET", f"/videos/{JOB_ID}", job("completed"))
        resumed = OpenRouterVideo().execute({"resume_job": resume, "prompt": "ignored",
                                             "output_path": str(tmp_path / "out.mp4")})
        assert resumed.success, resumed.error
        assert len(fake_http.by("POST", "/videos")) == 1

    def test_foreign_resume_job_rejected(self, key, fake_http, tmp_path):
        result = OpenRouterVideo().execute({"prompt": "x", "resume_job": {"id": "1", "tool": "pika_video"}})
        assert not result.success
        assert "does not belong" in result.error

    def test_job_checkpoint_has_no_image_bytes(self, key, fake_http, tmp_path, frame):
        happy_path(fake_http)
        job_path = tmp_path / "job.json"
        self.run(tmp_path, reference_image_path=str(frame), job_path=str(job_path))
        saved = json.loads(job_path.read_text())
        assert saved["id"] == JOB_ID
        url = saved["request"]["frame_images"][0]["image_url"]["url"]
        assert url.endswith(" bytes>")

    @pytest.mark.parametrize("status,retryable,hint", [
        (402, False, "credits"), (429, True, "Rate limited"), (400, False, ""),
    ])
    def test_http_errors(self, key, fake_http, tmp_path, status, retryable, hint):
        fake_http.add("POST", "/videos", FakeResponse(status, {"error": {"code": status, "message": "nope"}}))
        result = self.run(tmp_path)
        assert not result.success
        assert result.data["retryable"] is retryable
        assert hint in result.error
        assert len(fake_http.by("POST", "/videos")) == 1

    def test_dry_run_submits_nothing(self, key, fake_http, tmp_path, frame):
        result = self.run(tmp_path, reference_image_path=str(frame), dry_run=True)
        assert result.success
        assert result.data["estimated_cost_usd"] == 0.12
        assert fake_http.by("POST", "/videos") == []


# ------------------------------------------------------------------
# Selector routing
# ------------------------------------------------------------------

class TestSelector:
    def test_selector_passes_local_frame_without_fal_upload(self, key, fake_http, tmp_path, frame, monkeypatch):
        from tools.video import video_selector

        def boom(*_args, **_kwargs):
            raise AssertionError("local frames must not be uploaded to fal")

        monkeypatch.setattr("tools.video._shared.upload_image_fal", boom)
        happy_path(fake_http)
        result = video_selector.VideoSelector().execute({
            "preferred_tool": "openrouter_video",
            "prompt": "riders on a ridge",
            "operation": "image_to_video",
            "reference_image_path": str(frame),
            "duration": "4",
            "output_path": str(tmp_path / "out.mp4"),
        })
        assert result.success, result.error
        assert result.data["selected_tool"] == "openrouter_video"
        assert submitted_body(fake_http)["frame_images"][0]["frame_type"] == "first_frame"
