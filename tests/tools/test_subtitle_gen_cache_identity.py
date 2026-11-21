"""subtitle_gen must not serve a cached subtitle file made from different inputs.

The tool is deterministic, so with OPENMONTAGE_ASSET_CACHE=1 every call with an
output_path goes through the asset cache. Its key covered only segments, format
and max_words_per_cue, so a rerun with corrections, a new line length or a
highlight style restored the first call's file.
"""

import pytest

from lib.asset_cache import reset_default_asset_cache
from tools.subtitle.subtitle_gen import SubtitleGen

_FIELDS = sorted(set(SubtitleGen.input_schema["properties"]) - {"output_path"})

_SEGMENTS = [{
    "start": 0.0, "end": 1.0, "text": "teh fox",
    "words": [{"word": "teh", "start": 0.0, "end": 0.5},
              {"word": "fox", "start": 0.5, "end": 1.0}],
}]


def test_field_list_is_not_empty():
    assert {"corrections", "max_chars_per_line", "highlight_style"} <= set(_FIELDS)


@pytest.mark.parametrize("field", _FIELDS)
def test_every_subtitle_input_changes_the_cache_key(field):
    tool = SubtitleGen()
    base = {"segments": _SEGMENTS, "output_path": "out.srt"}
    assert tool.cache_key(base) != tool.cache_key({**base, field: "changed"})


def test_corrected_subtitles_are_written_not_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_ASSET_CACHE", "1")
    monkeypatch.setenv("OPENMONTAGE_ASSET_CACHE_DIR", str(tmp_path / "cache"))
    reset_default_asset_cache()
    tool = SubtitleGen()
    try:
        results = [
            tool.execute({"segments": _SEGMENTS, "output_path": str(tmp_path / out), **extra})
            for out, extra in [("raw.srt", {}),
                               ("fixed.srt", {"corrections": {"teh": "the"}})]
        ]
    finally:
        reset_default_asset_cache()

    assert all(r.success for r in results), [r.error for r in results]
    assert results[1].from_cache is False
    assert "teh fox" in (tmp_path / "raw.srt").read_text(encoding="utf-8")
    assert "the fox" in (tmp_path / "fixed.srt").read_text(encoding="utf-8")
