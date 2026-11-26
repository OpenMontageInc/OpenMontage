"""VoiceStudio TTS tool — local ElevenLabs-compatible alternative.

Requires VoiceStudio (https://github.com/debpalash/VoiceStudio) running locally.
Its backend exposes an ElevenLabs-compatible API at http://127.0.0.1:3900/elevenlabs.

No API key is required for loopback requests. Set VOICESTUDIO_URL to override
the base URL (e.g. for a remote instance that requires OMNIVOICE_API_KEY).

with_timestamps support (two-pass, fully local):
  Pass 1 — TTS:  POST /elevenlabs/v1/text-to-speech/{voice_id}  → audio bytes
  Pass 2 — STT:  POST /elevenlabs/v1/speech-to-text             → word timings
The result is assembled into the same .alignment.json sidecar and word_timestamps
structure that elevenlabs_tts produces, so downstream pipeline steps see no
difference.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)
from tools.tool_registry import register_tool


DEFAULT_BASE_URL = "http://127.0.0.1:3900/elevenlabs"
DEFAULT_VOICE_ID = "default"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
HEALTH_TIMEOUT = 2.0
STT_TIMEOUT = 120


@register_tool
class VoiceStudioTTS(BaseTool):
    name = "voicestudio_tts"
    version = "0.1.0"
    tier = ToolTier.LOCAL
    capability = "tts"
    provider = "voicestudio"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.LOCAL

    fallback_tools = ["openai_tts", "piper_tts"]

    capabilities = ["text_to_speech", "voice_cloning", "word_timestamps"]
    supports = {
        "voice_cloning": True,
        "multilingual": True,
        "offline": True,
        "native_audio": True,
    }
    best_for = [
        "privacy-sensitive or offline production",
        "zero-cost local TTS",
        "voice cloning without cloud keys",
    ]
    not_good_for = [
        "machines without VoiceStudio installed",
    ]

    install_instructions = (
        "Install and run VoiceStudio: https://github.com/debpalash/VoiceStudio\n"
        "Optionally set VOICESTUDIO_URL to override the default base URL "
        "(http://127.0.0.1:3900/elevenlabs).\n"
        "For non-loopback instances set OMNIVOICE_API_KEY."
    )

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to synthesize (max 4096 characters).",
            },
            "voice_id": {
                "type": "string",
                "description": (
                    "VoiceStudio voice profile name/ID, 'default', or an engine "
                    "preset. Call GET /v1/voices on VoiceStudio to list options."
                ),
                "default": DEFAULT_VOICE_ID,
            },
            "model_id": {
                "type": "string",
                "description": (
                    "ElevenLabs model ID (e.g. 'eleven_multilingual_v2') or a "
                    "VoiceStudio engine ID (e.g. 'voxcpm2'). Eleven-prefixed IDs "
                    "use whichever engine is active in the VoiceStudio app."
                ),
                "default": DEFAULT_MODEL_ID,
            },
            "output_format": {
                "type": "string",
                "enum": [
                    "mp3_44100_128",
                    "mp3_44100_192",
                    "pcm_16000",
                    "pcm_24000",
                    "pcm_44100",
                    "wav_44100",
                ],
                "default": DEFAULT_OUTPUT_FORMAT,
            },
            "speed": {
                "type": "number",
                "default": 1.0,
                "description": "Playback speed multiplier.",
            },
            "language_code": {
                "type": "string",
                "description": "BCP-47 language code hint (optional).",
            },
            "seed": {
                "type": "integer",
                "description": "Random seed for reproducibility (optional).",
            },
            "output_path": {
                "type": "string",
                "description": "Local filesystem path to write the audio file.",
            },
            "with_timestamps": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Return word-level timings (word_timestamps) and write a "
                    "<output>.alignment.json sidecar. Uses a fully local two-pass "
                    "approach: TTS → audio, then VoiceStudio STT → word timings. "
                    "No ElevenLabs API key required."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=1024, vram_mb=0, disk_mb=100, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=1)
    side_effects = ["writes audio file to output_path", "calls local VoiceStudio API"]

    def _base_url(self) -> str:
        return os.environ.get("VOICESTUDIO_URL", DEFAULT_BASE_URL).rstrip("/")

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = os.environ.get("OMNIVOICE_API_KEY")
        if key:
            h["xi-api-key"] = key
        return h

    def get_status(self) -> ToolStatus:
        try:
            health_url = (
                self._base_url().replace("/elevenlabs", "")
                + "/.well-known/voicestudio-speech"
            )
            resp = requests.get(health_url, timeout=HEALTH_TIMEOUT)
            if resp.ok:
                return ToolStatus.AVAILABLE
        except requests.exceptions.ConnectionError:
            pass
        except Exception:
            pass
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        try:
            result = self._generate(inputs)
        except Exception as exc:
            return ToolResult(success=False, error=f"VoiceStudio TTS failed: {exc}")
        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = 0.0
        return result

    def _generate(self, inputs: dict[str, Any]) -> ToolResult:
        text = inputs["text"]
        if len(text) > 4096:
            raise ValueError("text exceeds VoiceStudio's 4096-character limit")

        voice_id = inputs.get("voice_id", DEFAULT_VOICE_ID)
        model_id = inputs.get("model_id", DEFAULT_MODEL_ID)
        output_format = inputs.get("output_format", DEFAULT_OUTPUT_FORMAT)
        with_timestamps = bool(inputs.get("with_timestamps", False))

        # --- Pass 1: TTS ---
        tts_url = f"{self._base_url()}/v1/text-to-speech/{voice_id}"
        payload: dict[str, Any] = {"text": text, "model_id": model_id}
        for field in ("speed", "language_code", "seed"):
            if field in inputs:
                payload[field] = inputs[field]

        try:
            resp = requests.post(
                tts_url,
                json=payload,
                headers=self._headers(),
                params={"output_format": output_format},
                timeout=120,
            )
        except requests.exceptions.ConnectionError as exc:
            raise ConnectionError(
                f"VoiceStudio not reachable at {self._base_url()}: {exc}"
            ) from exc

        if not resp.ok:
            try:
                detail = resp.json().get("detail", {})
            except Exception:
                detail = resp.text
            raise RuntimeError(
                f"VoiceStudio TTS returned {resp.status_code}: {detail}"
            )

        audio_bytes = resp.content

        # --- Write audio file ---
        ext = "mp3" if output_format.startswith("mp3") else output_format.split("_")[0]
        output_path = Path(inputs.get("output_path", f"tts_output.{ext}"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio_bytes)

        artifacts = [str(output_path)]
        extra: dict[str, Any] = {}

        # --- Pass 2: STT for word timestamps (fully local, no ElevenLabs) ---
        if with_timestamps:
            words, alignment = self._transcribe_for_timestamps(
                audio_bytes, output_format, inputs.get("language_code")
            )
            alignment_path = output_path.with_name(output_path.name + ".alignment.json")
            alignment_path.write_text(
                json.dumps(
                    {"text": text, "alignment": alignment, "words": words},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            artifacts.append(str(alignment_path))
            extra = {
                "word_timestamps": words,
                "alignment_path": str(alignment_path),
                "alignment_matches_text": True,
            }

        return ToolResult(
            success=True,
            data={
                "provider": "voicestudio",
                "model": model_id,
                "voice_id": voice_id,
                "text_length": len(text),
                "output": str(output_path),
                "format": output_format,
                "cost_usd": 0.0,
                **extra,
            },
            artifacts=artifacts,
            model=model_id,
        )

    def _transcribe_for_timestamps(
        self,
        audio_bytes: bytes,
        output_format: str,
        language_code: Optional[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Pass 2: feed audio back to VoiceStudio STT to get word timings.

        Returns (words, alignment) in the same shape elevenlabs_tts produces,
        so downstream consumers (caption sync, word-cued animation) work unchanged.
        """
        stt_url = f"{self._base_url()}/v1/speech-to-text"

        if output_format.startswith("mp3"):
            mime, filename = "audio/mpeg", "audio.mp3"
        elif output_format.startswith("wav"):
            mime, filename = "audio/wav", "audio.wav"
        else:
            mime, filename = "audio/pcm", "audio.pcm"

        headers: dict[str, str] = {}
        key = os.environ.get("OMNIVOICE_API_KEY")
        if key:
            headers["xi-api-key"] = key

        form_data: dict[str, Any] = {}
        if language_code:
            form_data["language_code"] = language_code

        try:
            resp = requests.post(
                stt_url,
                headers=headers,
                files={"file": (filename, audio_bytes, mime)},
                data=form_data,
                timeout=STT_TIMEOUT,
            )
        except requests.exceptions.ConnectionError as exc:
            raise ConnectionError(
                f"VoiceStudio STT not reachable at {self._base_url()}: {exc}"
            ) from exc

        if not resp.ok:
            try:
                detail = resp.json().get("detail", {})
            except Exception:
                detail = resp.text
            raise RuntimeError(
                f"VoiceStudio STT returned {resp.status_code}: {detail}"
            )

        body = resp.json()

        # VoiceStudio STT returns ElevenLabs-compatible word array:
        # [{"text": "Hello", "start": 0.0, "end": 0.42, "type": "word"}, ...]
        raw_words = body.get("words") or []
        words = [
            {
                "word": w["text"],
                "start": round(w["start"], 3),
                "end": round(w["end"], 3),
            }
            for w in raw_words
            if w.get("type") == "word"
        ]

        alignment = _words_to_character_alignment(words)
        return words, alignment


def _words_to_character_alignment(
    words: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reconstruct a character-level alignment from word-level STT output.

    ElevenLabs alignment has per-character start/end times. VoiceStudio STT
    gives per-word times. We linearly interpolate character positions within
    each word so the .alignment.json sidecar stays readable by tools that
    consume the raw character arrays.
    """
    characters: list[str] = []
    char_starts: list[float] = []
    char_ends: list[float] = []

    for i, w in enumerate(words):
        word_text = w["word"]
        t_start = w["start"]
        t_end = w["end"]
        n = len(word_text)
        duration = t_end - t_start

        for j, ch in enumerate(word_text):
            ch_start = t_start + (duration * j / n) if n > 1 else t_start
            ch_end = t_start + (duration * (j + 1) / n) if n > 1 else t_end
            characters.append(ch)
            char_starts.append(round(ch_start, 4))
            char_ends.append(round(ch_end, 4))

        # space between words (not after the last)
        if i < len(words) - 1:
            characters.append(" ")
            char_starts.append(round(t_end, 4))
            char_ends.append(round(t_end, 4))

    return {
        "characters": characters,
        "character_start_times_seconds": char_starts,
        "character_end_times_seconds": char_ends,
    }
