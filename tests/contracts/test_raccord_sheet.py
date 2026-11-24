"""scripts/raccord_sheet.py: shot splitting and checklist rows (no ffmpeg needed for the pure parts)."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("raccord_sheet", Path(__file__).resolve().parents[2] / "scripts/raccord_sheet.py")
assert spec and spec.loader
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


def test_flicker_cuts_are_merged():
    # B9-like take: a 0.5-1.17 s flicker run then a real cut at 2.71
    shots = rs.shots_from_cuts([0.5, 0.67, 0.83, 1.0, 1.17, 2.71], 12.0, 0.25)
    assert shots == [(0.0, 0.5), (0.5, 0.83), (0.83, 1.17), (1.17, 2.71), (2.71, 12.0)]
    assert rs.shots_from_cuts([0.5, 0.67, 2.71], 12.0, 1.0) == [(0.0, 2.71), (2.71, 12.0)]


def test_cut_at_the_very_end_is_ignored():
    assert rs.shots_from_cuts([11.9], 12.0, 0.25) == [(0.0, 12.0)]


def test_checklist_has_one_row_per_cut(tmp_path, monkeypatch):
    from PIL import Image

    durations = {"a": 9.0, "b": 12.0}
    monkeypatch.setattr(rs, "probe_duration", lambda v: durations[v.stem])
    monkeypatch.setattr(rs, "detect_cuts", lambda v: [] if v.stem == "a" else [4.0, 8.0])
    monkeypatch.setattr(rs, "grab", lambda v, t, w, c: Image.new("RGB", (30, 50)))
    assert rs.main([str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4"), "--out", str(tmp_path / "out"), "--max-panels", "4"]) == 0
    rows = [l for l in (tmp_path / "out/raccord_checklist.md").read_text().splitlines() if "→" in l]
    assert [r.split("|")[1].strip() for r in rows] == ["a → b (between takes)", "b s1 → s2", "b s2 → s3"]
    # b: prev END + 3 shots x 2 = 7 panels -> split in 2 sheets of <= 4
    assert sorted(p.name for p in (tmp_path / "out").glob("b*.jpg")) == ["b_1.jpg", "b_2.jpg"]
    with pytest.raises(SystemExit):
        rs.main([])
