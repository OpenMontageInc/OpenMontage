"""scripts/prompt_preflight.py: the pre-take checks catch the E03 defect classes before any spend."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("prompt_preflight", Path(__file__).resolve().parents[2] / "scripts/prompt_preflight.py")
assert spec and spec.loader
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)

GOOD = """References: @Image1 is the style. @Image2 is the penthouse plate. @Image3 is Mara. @Audio1 is Mara's voice.
Two shots, 8 seconds. Exactly two people in frame.
LOCATION MAP (penthouse): a full-height bedroom door, taller than an adult, on the LEFT.
Shot 1 (0-4s): Medium shot on Mara at frame left, eyes toward frame right. She says: "I came." Hard cut.
Shot 2 (4-8s): Close-up on Hale at frame right, she nods.
FINAL FRAME: Hale at frame right, Mara at frame left by the elevator.
CONTINUITY LOCKS: Mara frame LEFT, Hale frame RIGHT."""


def clip(tmp_path, prompt=GOOD, **kw):
    img = [tmp_path / n for n in ("A13.png", "L5a.png", "A1d.png")]
    for f in img:
        f.write_bytes(b"x")
    aud = tmp_path / "mara.mp3"
    aud.write_bytes(b"x")
    c = {"prompt": prompt, "images": [str(f) for f in img], "audio": [str(aud)], "seconds": 8, "cast": ["mara", "vasseur"],
         "speakers": ["mara"], "location": "penthouse", "mode": "reference"}
    c.update(kw)
    return c


def run(tmp_path, clips, *extra):
    src = tmp_path / "clips.json"
    src.write_text(json.dumps(clips))
    out = tmp_path / "report.md"
    code = pf.main([str(src), "--out", str(out), "--no-probe", *extra])
    return code, out.read_text()


def test_clean_clip_passes(tmp_path):
    code, rep = run(tmp_path, {"B1": clip(tmp_path)}, "--trait", "scar")
    assert code == 0 and "- ok" in rep


def test_trait_in_locks_only_is_flagged(tmp_path):
    p = GOOD.replace("CONTINUITY LOCKS: ", "KAIA LOCK: crescent scar on her inner LEFT wrist.\nCONTINUITY LOCKS: crescent scar on the wrist. ")
    _, rep = run(tmp_path, {"B1": clip(tmp_path, p)}, "--trait", "scar")
    assert "[traits] 'scar' cited 2x but no shot shows it" in rep


def test_same_location_without_previous_frame_is_flagged(tmp_path):
    _, rep = run(tmp_path, {"B1": clip(tmp_path), "B2": clip(tmp_path)})
    assert "same location as B1" in rep
    _, rep = run(tmp_path, {"B1": clip(tmp_path), "B2": clip(tmp_path, prev_frame=True)})
    assert "same location as B1" not in rep


def test_unchecked_plate_blocks_and_checked_plate_passes(tmp_path):
    code, rep = run(tmp_path, {"B1": clip(tmp_path)}, "--plate-glob", "/L")
    assert code == 1 and "plate L5a.png not scale-checked" in rep
    ok = tmp_path / "plates_ok.json"
    ok.write_text(json.dumps(["L5a.png"]))
    code, _ = run(tmp_path, {"B1": clip(tmp_path)}, "--plate-glob", "/L", "--plates-ok", str(ok))
    assert code == 0


def test_structure_refs_sizing_and_handoff(tmp_path):
    p = GOOD.replace("Shot 2 (4-8s)", "Shot 2 (5-8s)").replace("@Audio1 is", "@Audio2 is").replace(
        "FINAL FRAME: Hale at frame right, Mara at frame left by the elevator.", "FINAL FRAME: Theo's face, waiting.")
    code, rep = run(tmp_path, {"B1": clip(tmp_path, p, cast=["a", "b", "c"], speakers=["a", "b", "c"], seconds=14)})
    assert code == 1
    for needle in ("gap/overlap between shot 1", "@Audio2 cited but only 1 attached", "3 speakers > 2", "max 3/12s mixed",
                   "names no position"):
        assert needle in rep, needle
