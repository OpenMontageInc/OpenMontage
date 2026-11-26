"""VoiceStudio TTS — integration tests (requires VoiceStudio running locally).

These tests make real HTTP calls to http://127.0.0.1:3900 and are skipped
unless OPENMONTAGE_ALLOW_NETWORK=1 is set.

Run with:
    OPENMONTAGE_ALLOW_NETWORK=1 pytest tests/tools/test_voicestudio_tts_integration.py -v
"""

from __future__ import annotations

import json

import pytest

from tools.audio.voicestudio_tts import VoiceStudioTTS
from tools.base_tool import ToolStatus


pytestmark = pytest.mark.live_api


@pytest.fixture(scope="module")
def tool():
    t = VoiceStudioTTS()
    if t.get_status() != ToolStatus.AVAILABLE:
        pytest.skip("VoiceStudio not running at http://127.0.0.1:3900")
    return t


def test_get_status_is_available(tool):
    assert tool.get_status() == ToolStatus.AVAILABLE


def test_basic_tts_generates_audio(tool, tmp_path):
    out = tmp_path / "voice.mp3"
    result = tool.execute({"text": "Hello from VoiceStudio.", "output_path": str(out)})

    assert result.success, result.error
    assert out.exists()
    assert out.stat().st_size > 1000  # a real MP3 is never this small
    assert result.data["provider"] == "voicestudio"
    assert result.data["cost_usd"] == 0.0
    assert "word_timestamps" not in result.data


def test_tts_respects_output_format(tool, tmp_path):
    out = tmp_path / "voice.wav"
    result = tool.execute({
        "text": "Testing wav output.",
        "output_format": "wav_44100",
        "output_path": str(out),
    })

    assert result.success, result.error
    assert out.exists()
    assert out.read_bytes()[:4] == b"RIFF"  # WAV magic bytes


def test_with_timestamps_returns_word_list(tool, tmp_path):
    text = "The quick brown fox."
    out = tmp_path / "voice.mp3"
    result = tool.execute({"text": text, "with_timestamps": True, "output_path": str(out)})

    assert result.success, result.error
    words = result.data["word_timestamps"]
    assert isinstance(words, list)
    assert len(words) >= 4
    for w in words:
        assert "word" in w
        assert isinstance(w["start"], float)
        assert isinstance(w["end"], float)
        assert w["end"] >= w["start"]


def test_with_timestamps_words_are_ordered(tool, tmp_path):
    out = tmp_path / "voice.mp3"
    result = tool.execute({
        "text": "One two three four five.",
        "with_timestamps": True,
        "output_path": str(out),
    })

    assert result.success, result.error
    starts = [w["start"] for w in result.data["word_timestamps"]]
    assert starts == sorted(starts), "word timestamps must be in chronological order"


def test_with_timestamps_writes_alignment_json(tool, tmp_path):
    out = tmp_path / "voice.mp3"
    result = tool.execute({
        "text": "Hello world.",
        "with_timestamps": True,
        "output_path": str(out),
    })

    assert result.success, result.error
    alignment_path = tmp_path / "voice.mp3.alignment.json"
    assert alignment_path.exists()

    saved = json.loads(alignment_path.read_text(encoding="utf-8"))
    assert saved["text"] == "Hello world."
    assert saved["words"] == result.data["word_timestamps"]
    assert "characters" in saved["alignment"]
    assert len(saved["alignment"]["characters"]) > 0


def test_with_timestamps_coverage(tool, tmp_path):
    """Every word in the input should appear somewhere in word_timestamps."""
    text = "Integration test coverage check."
    out = tmp_path / "voice.mp3"
    result = tool.execute({"text": text, "with_timestamps": True, "output_path": str(out)})

    assert result.success, result.error
    returned_words = {w["word"].lower().strip(".,!?") for w in result.data["word_timestamps"]}
    input_words = {w.lower().strip(".,!?") for w in text.split()}
    assert input_words.issubset(returned_words), (
        f"Missing words in timestamps: {input_words - returned_words}"
    )


def test_4096_char_limit_enforced(tool, tmp_path):
    result = tool.execute({"text": "x" * 4097, "output_path": str(tmp_path / "a.mp3")})
    assert not result.success
    assert "4096" in result.error
