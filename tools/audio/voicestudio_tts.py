"""VoiceStudio TTS tool — local ElevenLabs-compatible alternative.

Requires VoiceStudio (https://github.com/debpalash/VoiceStudio) running locally.
Its backend exposes an ElevenLabs-compatible API at http://127.0.0.1:3900/elevenlabs.

No API key is required for loopback requests. Set VOICESTUDIO_URL to override
the base URL (e.g. for a remote instance that requires OMNIVOICE_API_KEY).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import requests

from lib.tool_base import BaseTool, ToolRuntime, ToolTier
from lib.tool_registry import register_tool
from lib.tool_status import ToolStatus


DEFAULT_BASE_URL = "http://127.0.0.1:3900/elevenlabs"
DEFAULT_VOICE_ID = "default"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"  # mapped to active engine by VoiceStudio
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
HEALTH_TIMEOUT = 2.0  # seconds


@register_tool
class VoiceStudioTTS(BaseTool):
    name = "voicestudio_tts"
    description = (
        "Local ElevenLabs-compatible TTS via VoiceStudio "
        "(https://github.com/debpalash/VoiceStudio). "
        "Requires VoiceStudio running on this machine (or VOICESTUDIO_URL set). "
        "Supports voice cloning, 646 languages, and streaming. "
        "No cloud API key needed — fully local."
    )
    capability = "tts"
    provider = "voicestudio"
    tier = ToolTier.LOCAL
    runtime = ToolRuntime.LOCAL

    fallback_tools = ["openai_tts", "piper_tts"]
    max_retries = 1

    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to synthesize (max 4096 characters).",
            },
            "voice_id": {
                "type": "string",
                "description": (
                    "VoiceStudio voice profile name/ID, 'default', or an engine "
                    "preset. Run GET /v1/voices against VoiceStudio to list available "
                    "voices. Defaults to 'default'."
                ),
                "default": DEFAULT_VOICE_ID,
            },
            "model_id": {
                "type": "string",
                "description": (
                    "ElevenLabs model ID (e.g. 'eleven_multilingual_v2') or a "
                    "VoiceStudio engine ID (e.g. 'voxcpm2'). Eleven-prefixed IDs "
                    "use whichever engine is active in the app."
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
                "description": "Output audio format.",
                "default": DEFAULT_OUTPUT_FORMAT,
            },
            "speed": {
                "type": "number",
                "description": "Playback speed multiplier (applied by VoiceStudio).",
                "default": 1.0,
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
        },
        "required": ["text"],
    }

    def _base_url(self) -> str:
        return os.environ.get("VOICESTUDIO_URL", DEFAULT_BASE_URL).rstrip("/")

    def _api_key(self) -> Optional[str]:
        return os.environ.get("OMNIVOICE_API_KEY")

    def get_status(self) -> ToolStatus:
        try:
            url = self._base_url().replace("/elevenlabs", "") + "/.well-known/voicestudio-speech"
            resp = requests.get(url, timeout=HEALTH_TIMEOUT)
            if resp.ok:
                return ToolStatus.AVAILABLE
        except requests.exceptions.ConnectionError:
            pass
        except Exception:
            pass
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict) -> float:
        # VoiceStudio is local — marginal cost is effectively zero
        return 0.0

    def run(self, inputs: dict) -> dict:
        text = inputs["text"]
        if len(text) > 4096:
            return {"error": "text exceeds VoiceStudio's 4096-character limit"}

        voice_id = inputs.get("voice_id", DEFAULT_VOICE_ID)
        model_id = inputs.get("model_id", DEFAULT_MODEL_ID)
        output_format = inputs.get("output_format", DEFAULT_OUTPUT_FORMAT)
        output_path = inputs.get("output_path")

        url = f"{self._base_url()}/v1/text-to-speech/{voice_id}"

        headers = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["xi-api-key"] = api_key

        payload: dict = {
            "text": text,
            "model_id": model_id,
        }
        if "speed" in inputs:
            payload["speed"] = inputs["speed"]
        if "language_code" in inputs:
            payload["language_code"] = inputs["language_code"]
        if "seed" in inputs:
            payload["seed"] = inputs["seed"]

        params = {"output_format": output_format}

        start = time.time()
        try:
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                params=params,
                timeout=120,
            )
        except requests.exceptions.ConnectionError as exc:
            return {"error": f"VoiceStudio not reachable at {self._base_url()}: {exc}"}

        elapsed = time.time() - start

        if not resp.ok:
            try:
                detail = resp.json().get("detail", {})
            except Exception:
                detail = resp.text
            return {
                "error": f"VoiceStudio returned {resp.status_code}",
                "detail": detail,
            }

        audio_bytes = resp.content

        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(audio_bytes)

        ext = "mp3" if output_format.startswith("mp3") else output_format.split("_")[0]
        return {
            "audio_bytes": audio_bytes if not output_path else None,
            "output_path": output_path,
            "output_format": output_format,
            "file_extension": ext,
            "duration_seconds": elapsed,
            "provider": "voicestudio",
            "voice_id": voice_id,
            "model_id": model_id,
            "characters": len(text),
            "cost_usd": 0.0,
        }
