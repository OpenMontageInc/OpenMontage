"""VoiceStudio / ElevenLabs output parity tests.

Verifies that VoiceStudioTTS produces word_timestamps and .alignment.json
in the same shape as ElevenLabsTTS, so downstream pipeline steps (caption
sync, word-cued animation) work identically regardless of which provider ran.

These tests run against live services and are skipped by default.

Run with:
    ELEVENLABS_API_KEY=sk-... OPENMONTAGE_ALLOW_NETWORK=1 \
        pytest tests/tools/test_voicestudio_elevenlabs_parity.py -v
"""

from __future__ import annotations

import json
import os

import pytest

from tools.audio.elevenlabs_tts import ElevenLabsTTS
from tools.audio.voicestudio_tts import VoiceStudioTTS
from tools.base_tool import ToolStatus


pytestmark = pytest.mark.live_api

TEXT = "The quick brown fox jumps over the lazy dog."


@pytest.fixture(scope="module")
def vs_tool():
    t = VoiceStudioTTS()
    if t.get_status() != ToolStatus.AVAILABLE:
        pytest.skip("VoiceStudio not running at http://127.0.0.1:3900")
    return t


@pytest.fixture(scope="module")
def el_tool():
    if not os.environ.get("ELEVENLABS_API_KEY"):
        pytest.skip("ELEVENLABS_API_KEY not set")
    t = ElevenLabsTTS()
    if t.get_status() != ToolStatus.AVAILABLE:
        pytest.skip("ElevenLabs unavailable")
    return t


@pytest.fixture(scope="module")
def vs_result(vs_tool, tmp_path_factory):
    out = tmp_path_factory.mktemp("vs") / "voice.mp3"
    result = vs_tool.execute({"text": TEXT, "with_timestamps": True, "output_path": str(out)})
    assert result.success, f"VoiceStudio failed: {result.error}"
    return result


@pytest.fixture(scope="module")
def el_result(el_tool, tmp_path_factory):
    out = tmp_path_factory.mktemp("el") / "voice.mp3"
    result = el_tool.execute({"text": TEXT, "with_timestamps": True, "output_path": str(out)})
    assert result.success, f"ElevenLabs failed: {result.error}"
    return result


# ---------------------------------------------------------------------------
# Shape parity (word_timestamps)
# ---------------------------------------------------------------------------


def test_both_return_word_timestamps(vs_result, el_result):
    assert "word_timestamps" in vs_result.data
    assert "word_timestamps" in el_result.data


def test_word_timestamps_have_same_keys(vs_result, el_result):
    vs_keys = set(vs_result.data["word_timestamps"][0].keys())
    el_keys = set(el_result.data["word_timestamps"][0].keys())
    assert vs_keys == el_keys == {"word", "start", "end"}


def test_word_timestamps_values_are_floats(vs_result, el_result):
    for result in (vs_result, el_result):
        for w in result.data["word_timestamps"]:
            assert isinstance(w["start"], float)
            assert isinstance(w["end"], float)


def test_word_timestamps_non_overlapping(vs_result, el_result):
    for result in (vs_result, el_result):
        words = result.data["word_timestamps"]
        for i in range(len(words) - 1):
            assert words[i]["end"] <= words[i + 1]["start"] + 0.05, (
                f"Overlapping timestamps at index {i}: {words[i]} / {words[i+1]}"
            )


def test_word_count_roughly_matches(vs_result, el_result):
    """Allow ±2 words difference (punctuation handling may vary)."""
    vs_count = len(vs_result.data["word_timestamps"])
    el_count = len(el_result.data["word_timestamps"])
    assert abs(vs_count - el_count) <= 2, (
        f"Word count mismatch: VoiceStudio={vs_count}, ElevenLabs={el_count}"
    )


def test_timing_duration_in_same_ballpark(vs_result, el_result):
    """Total audio duration should be within 20% between providers."""
    vs_end = vs_result.data["word_timestamps"][-1]["end"]
    el_end = el_result.data["word_timestamps"][-1]["end"]
    ratio = max(vs_end, el_end) / min(vs_end, el_end)
    assert ratio < 1.2, (
        f"Duration mismatch too large: VoiceStudio={vs_end:.2f}s, ElevenLabs={el_end:.2f}s"
    )


# ---------------------------------------------------------------------------
# Shape parity (.alignment.json)
# ---------------------------------------------------------------------------


def test_both_write_alignment_json(vs_result, el_result):
    assert "alignment_path" in vs_result.data
    assert "alignment_path" in el_result.data


def test_alignment_json_has_same_top_level_keys(vs_result, el_result):
    vs_saved = json.loads(open(vs_result.data["alignment_path"]).read())
    el_saved = json.loads(open(el_result.data["alignment_path"]).read())
    assert set(vs_saved.keys()) == set(el_saved.keys()) == {"text", "alignment", "words"}


def test_alignment_json_character_arrays_present(vs_result, el_result):
    for result in (vs_result, el_result):
        saved = json.loads(open(result.data["alignment_path"]).read())
        al = saved["alignment"]
        assert "characters" in al
        assert "character_start_times_seconds" in al
        assert "character_end_times_seconds" in al
        n = len(al["characters"])
        assert len(al["character_start_times_seconds"]) == n
        assert len(al["character_end_times_seconds"]) == n


def test_alignment_json_words_match_word_timestamps(vs_result, el_result):
    for result in (vs_result, el_result):
        saved = json.loads(open(result.data["alignment_path"]).read())
        assert saved["words"] == result.data["word_timestamps"]


# ---------------------------------------------------------------------------
# Artifacts parity
# ---------------------------------------------------------------------------


def test_both_include_alignment_path_in_artifacts(vs_result, el_result):
    for result in (vs_result, el_result):
        assert any("alignment.json" in a for a in result.artifacts)


def test_both_report_non_negative_duration(vs_result, el_result):
    for result in (vs_result, el_result):
        assert result.duration_seconds >= 0
