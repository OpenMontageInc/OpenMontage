"""VoiceStudio TTS — unit tests (no network, no VoiceStudio process required).

Covers:
- TTS pass: correct URL, headers, payload, and audio file written
- STT pass: only called when with_timestamps=True; word_timestamps and
  .alignment.json sidecar match the shape elevenlabs_tts produces
- _words_to_character_alignment: linear interpolation correctness
- get_status: AVAILABLE / UNAVAILABLE based on health probe
- Error paths: 4096-char limit, TTS HTTP error, STT HTTP error
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from tools.audio.voicestudio_tts import VoiceStudioTTS, _words_to_character_alignment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_MP3 = b"ID3\x03\x00\x00\x00fake-mp3-bytes"

STT_WORDS = [
    {"text": "Hello", "start": 0.0, "end": 0.42, "type": "word"},
    {"text": "world", "start": 0.50, "end": 0.91, "type": "word"},
]


def _ok_tts_response(audio: bytes = FAKE_MP3) -> Mock:
    r = Mock()
    r.ok = True
    r.content = audio
    return r


def _ok_stt_response(words=None) -> Mock:
    r = Mock()
    r.ok = True
    r.json.return_value = {"words": words or STT_WORDS}
    return r


def _err_response(status: int, detail: str) -> Mock:
    r = Mock()
    r.ok = False
    r.status_code = status
    r.json.return_value = {"detail": {"status": "error", "message": detail}}
    return r


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


def test_get_status_available_when_health_ok(monkeypatch):
    monkeypatch.setattr("requests.get", Mock(return_value=Mock(ok=True)))
    assert VoiceStudioTTS().get_status().name == "AVAILABLE"


def test_get_status_unavailable_when_connection_error(monkeypatch):
    import requests as req
    monkeypatch.setattr("requests.get", Mock(side_effect=req.exceptions.ConnectionError))
    assert VoiceStudioTTS().get_status().name == "UNAVAILABLE"


def test_get_status_unavailable_when_health_not_ok(monkeypatch):
    monkeypatch.setattr("requests.get", Mock(return_value=Mock(ok=False)))
    assert VoiceStudioTTS().get_status().name == "UNAVAILABLE"


def test_get_status_uses_custom_base_url(monkeypatch):
    monkeypatch.setenv("VOICESTUDIO_URL", "http://10.0.0.5:3900/elevenlabs")
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return Mock(ok=True)

    monkeypatch.setattr("requests.get", fake_get)
    VoiceStudioTTS().get_status()
    assert captured["url"] == "http://10.0.0.5:3900/.well-known/voicestudio-speech"


# ---------------------------------------------------------------------------
# TTS pass — no timestamps
# ---------------------------------------------------------------------------


def test_basic_tts_writes_audio_file(monkeypatch, tmp_path):
    monkeypatch.setattr("requests.post", Mock(return_value=_ok_tts_response()))
    out = tmp_path / "voice.mp3"

    result = VoiceStudioTTS().execute({"text": "Hello world", "output_path": str(out)})

    assert result.success, result.error
    assert out.read_bytes() == FAKE_MP3
    assert result.data["provider"] == "voicestudio"
    assert result.data["cost_usd"] == 0.0
    assert "word_timestamps" not in result.data
    assert not (tmp_path / "voice.mp3.alignment.json").exists()


def test_tts_posts_to_correct_url(monkeypatch, tmp_path):
    post = Mock(return_value=_ok_tts_response())
    monkeypatch.setattr("requests.post", post)

    VoiceStudioTTS().execute({"text": "Hi", "output_path": str(tmp_path / "a.mp3")})

    url = post.call_args.args[0]
    assert url == "http://127.0.0.1:3900/elevenlabs/v1/text-to-speech/default"


def test_tts_passes_optional_fields(monkeypatch, tmp_path):
    post = Mock(return_value=_ok_tts_response())
    monkeypatch.setattr("requests.post", post)

    VoiceStudioTTS().execute({
        "text": "Hi",
        "voice_id": "my-clone",
        "model_id": "voxcpm2",
        "speed": 1.2,
        "language_code": "fr-FR",
        "seed": 42,
        "output_path": str(tmp_path / "a.mp3"),
    })

    payload = post.call_args.kwargs["json"]
    assert payload["model_id"] == "voxcpm2"
    assert payload["speed"] == 1.2
    assert payload["language_code"] == "fr-FR"
    assert payload["seed"] == 42
    assert "with_timestamps" not in payload  # tool option, not an API field


def test_tts_sends_api_key_header_when_env_set(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNIVOICE_API_KEY", "secret-key")
    post = Mock(return_value=_ok_tts_response())
    monkeypatch.setattr("requests.post", post)

    VoiceStudioTTS().execute({"text": "Hi", "output_path": str(tmp_path / "a.mp3")})

    assert post.call_args.kwargs["headers"]["xi-api-key"] == "secret-key"


def test_tts_no_api_key_header_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    post = Mock(return_value=_ok_tts_response())
    monkeypatch.setattr("requests.post", post)

    VoiceStudioTTS().execute({"text": "Hi", "output_path": str(tmp_path / "a.mp3")})

    assert "xi-api-key" not in post.call_args.kwargs["headers"]


def test_tts_output_format_in_query_params(monkeypatch, tmp_path):
    post = Mock(return_value=_ok_tts_response())
    monkeypatch.setattr("requests.post", post)

    VoiceStudioTTS().execute({
        "text": "Hi",
        "output_format": "pcm_24000",
        "output_path": str(tmp_path / "a.pcm"),
    })

    assert post.call_args.kwargs["params"]["output_format"] == "pcm_24000"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_text_over_4096_chars_fails_before_http(monkeypatch, tmp_path):
    post = Mock()
    monkeypatch.setattr("requests.post", post)

    result = VoiceStudioTTS().execute({
        "text": "x" * 4097,
        "output_path": str(tmp_path / "a.mp3"),
    })

    assert not result.success
    assert "4096" in result.error
    post.assert_not_called()


def test_tts_http_error_returns_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("requests.post", Mock(return_value=_err_response(422, "voice_not_found")))

    result = VoiceStudioTTS().execute({"text": "Hi", "output_path": str(tmp_path / "a.mp3")})

    assert not result.success
    assert "422" in result.error


def test_tts_connection_error_returns_failure(monkeypatch, tmp_path):
    import requests as req
    monkeypatch.setattr("requests.post", Mock(side_effect=req.exceptions.ConnectionError("refused")))

    result = VoiceStudioTTS().execute({"text": "Hi", "output_path": str(tmp_path / "a.mp3")})

    assert not result.success
    assert "not reachable" in result.error


# ---------------------------------------------------------------------------
# Two-pass with_timestamps
# ---------------------------------------------------------------------------


def test_with_timestamps_calls_stt_after_tts(monkeypatch, tmp_path):
    post = Mock(side_effect=[_ok_tts_response(), _ok_stt_response()])
    monkeypatch.setattr("requests.post", post)

    result = VoiceStudioTTS().execute({
        "text": "Hello world",
        "with_timestamps": True,
        "output_path": str(tmp_path / "voice.mp3"),
    })

    assert result.success, result.error
    assert post.call_count == 2
    tts_url = post.call_args_list[0].args[0]
    stt_url = post.call_args_list[1].args[0]
    assert "text-to-speech" in tts_url
    assert "speech-to-text" in stt_url


def test_with_timestamps_false_skips_stt(monkeypatch, tmp_path):
    post = Mock(return_value=_ok_tts_response())
    monkeypatch.setattr("requests.post", post)

    VoiceStudioTTS().execute({
        "text": "Hello",
        "with_timestamps": False,
        "output_path": str(tmp_path / "a.mp3"),
    })

    assert post.call_count == 1


def test_with_timestamps_returns_word_timestamps(monkeypatch, tmp_path):
    monkeypatch.setattr("requests.post", Mock(side_effect=[
        _ok_tts_response(), _ok_stt_response(),
    ]))

    result = VoiceStudioTTS().execute({
        "text": "Hello world",
        "with_timestamps": True,
        "output_path": str(tmp_path / "voice.mp3"),
    })

    words = result.data["word_timestamps"]
    assert words == [
        {"word": "Hello", "start": 0.0, "end": 0.42},
        {"word": "world", "start": 0.5, "end": 0.91},
    ]


def test_with_timestamps_writes_alignment_json(monkeypatch, tmp_path):
    monkeypatch.setattr("requests.post", Mock(side_effect=[
        _ok_tts_response(), _ok_stt_response(),
    ]))
    out = tmp_path / "voice.mp3"

    result = VoiceStudioTTS().execute({
        "text": "Hello world",
        "with_timestamps": True,
        "output_path": str(out),
    })

    alignment_path = tmp_path / "voice.mp3.alignment.json"
    assert alignment_path.exists()
    saved = json.loads(alignment_path.read_text(encoding="utf-8"))
    assert saved["text"] == "Hello world"
    assert saved["words"] == result.data["word_timestamps"]
    assert "characters" in saved["alignment"]
    assert "character_start_times_seconds" in saved["alignment"]
    assert "character_end_times_seconds" in saved["alignment"]
    assert str(alignment_path) in result.artifacts


def test_with_timestamps_stt_receives_tts_audio_bytes(monkeypatch, tmp_path):
    audio = b"unique-audio-content-xyz"
    post = Mock(side_effect=[_ok_tts_response(audio), _ok_stt_response()])
    monkeypatch.setattr("requests.post", post)

    VoiceStudioTTS().execute({
        "text": "Hi",
        "with_timestamps": True,
        "output_path": str(tmp_path / "a.mp3"),
    })

    stt_files = post.call_args_list[1].kwargs["files"]
    _, sent_bytes, _ = stt_files["file"]
    assert sent_bytes == audio


def test_with_timestamps_passes_language_code_to_stt(monkeypatch, tmp_path):
    post = Mock(side_effect=[_ok_tts_response(), _ok_stt_response()])
    monkeypatch.setattr("requests.post", post)

    VoiceStudioTTS().execute({
        "text": "Bonjour",
        "with_timestamps": True,
        "language_code": "fr-FR",
        "output_path": str(tmp_path / "a.mp3"),
    })

    stt_data = post.call_args_list[1].kwargs["data"]
    assert stt_data.get("language_code") == "fr-FR"


def test_with_timestamps_stt_error_returns_failure(monkeypatch, tmp_path):
    post = Mock(side_effect=[_ok_tts_response(), _err_response(500, "stt_failed")])
    monkeypatch.setattr("requests.post", post)

    result = VoiceStudioTTS().execute({
        "text": "Hi",
        "with_timestamps": True,
        "output_path": str(tmp_path / "a.mp3"),
    })

    assert not result.success
    assert "500" in result.error


def test_with_timestamps_filters_non_word_stt_tokens(monkeypatch, tmp_path):
    stt_words = [
        {"text": "Hello", "start": 0.0, "end": 0.4, "type": "word"},
        {"text": ",",     "start": 0.4, "end": 0.4, "type": "punctuation"},
        {"text": " ",     "start": 0.4, "end": 0.5, "type": "spacing"},
        {"text": "world", "start": 0.5, "end": 0.9, "type": "word"},
    ]
    post = Mock(side_effect=[_ok_tts_response(), _ok_stt_response(stt_words)])
    monkeypatch.setattr("requests.post", post)

    result = VoiceStudioTTS().execute({
        "text": "Hello, world",
        "with_timestamps": True,
        "output_path": str(tmp_path / "a.mp3"),
    })

    assert [w["word"] for w in result.data["word_timestamps"]] == ["Hello", "world"]


# ---------------------------------------------------------------------------
# _words_to_character_alignment
# ---------------------------------------------------------------------------


def test_character_alignment_single_word():
    words = [{"word": "Hi", "start": 0.0, "end": 0.4}]
    al = _words_to_character_alignment(words)
    assert al["characters"] == ["H", "i"]
    assert al["character_start_times_seconds"] == [0.0, 0.2]
    assert al["character_end_times_seconds"] == [0.2, 0.4]


def test_character_alignment_two_words_adds_space():
    words = [
        {"word": "Hi", "start": 0.0, "end": 0.4},
        {"word": "yo", "start": 0.5, "end": 0.9},
    ]
    al = _words_to_character_alignment(words)
    assert " " in al["characters"]
    space_idx = al["characters"].index(" ")
    assert al["character_start_times_seconds"][space_idx] == 0.4


def test_character_alignment_no_trailing_space():
    words = [
        {"word": "A", "start": 0.0, "end": 0.1},
        {"word": "B", "start": 0.2, "end": 0.3},
    ]
    al = _words_to_character_alignment(words)
    assert al["characters"][-1] != " "


def test_character_alignment_single_char_word():
    words = [{"word": "I", "start": 0.1, "end": 0.2}]
    al = _words_to_character_alignment(words)
    assert al["characters"] == ["I"]
    assert al["character_start_times_seconds"] == [0.1]
    assert al["character_end_times_seconds"] == [0.2]


def test_character_alignment_arrays_same_length():
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.6, "end": 1.1},
    ]
    al = _words_to_character_alignment(words)
    n = len(al["characters"])
    assert len(al["character_start_times_seconds"]) == n
    assert len(al["character_end_times_seconds"]) == n


# ---------------------------------------------------------------------------
# Schema and cost
# ---------------------------------------------------------------------------


def test_estimate_cost_is_zero():
    assert VoiceStudioTTS().estimate_cost({"text": "x" * 1000}) == 0.0


def test_with_timestamps_default_is_false():
    props = VoiceStudioTTS.input_schema["properties"]
    assert props["with_timestamps"]["default"] is False
