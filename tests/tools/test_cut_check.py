"""cut_check: contract, discovery, status, and detection on synthetic media.

Each synthetic clip is built with ffmpeg and carries one known problem at a known time. Those tests are skipped
when ffmpeg is not installed; the contract tests always run.
"""
import json
import shutil
import subprocess
from unittest.mock import patch

import pytest

from tools.analysis.cut_check import CutCheck, Thresholds, drift_ms, tc
from tools.base_tool import ToolStatus

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg and ffprobe on PATH")
SRC = "s=320x240:r=24"
ENC = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le"]


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def kinds(result, kind):
    assert result.success, result.error
    return [f for f in result.data["findings"] if f["kind"] == kind]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("cut_check")
    shots3 = (f"testsrc2={SRC}:d=3[a];testsrc2={SRC}:d=3,negate[b];"
              f"testsrc2={SRC}:d=3,hflip,hue=h=90[c];[a][b][c]concat=n=3:v=1:a=0[v]")
    ffmpeg("-filter_complex", f"{shots3};anoisesrc=d=9:c=pink:a=0.02,aformat=channel_layouts=stereo[s]",
           "-map", "[v]", "-map", "[s]", *ENC, str(d / "clean.mkv"))
    ffmpeg("-filter_complex", f"{shots3};anoisesrc=d=3:c=pink:a=0.01[x];anoisesrc=d=3:c=pink:a=0.056[y];"
           "anoisesrc=d=3:c=pink:a=0.01[z];[x][y][z]concat=n=3:v=0:a=1,aformat=channel_layouts=stereo[s]",
           "-map", "[v]", "-map", "[s]", *ENC, str(d / "jump.mkv"))
    ffmpeg("-f", "lavfi", "-i", f"testsrc2={SRC}:d=10", "-f", "lavfi", "-i",
           "aevalsrc=exprs='0.05*sin(2*PI*220*t)+if(eq(n\\,240000)\\,0.9\\,0)':s=48000:d=10",
           *ENC, str(d / "click.mkv"))
    ffmpeg("-f", "lavfi", "-i", f"testsrc2={SRC}:d=10,drawbox=t=fill:c=black:enable='between(t,4,4.5)'",
           "-f", "lavfi", "-i", "anoisesrc=d=10:c=pink:a=0.02", *ENC, str(d / "black.mkv"))
    ffmpeg("-f", "lavfi", "-i", f"testsrc2={SRC}:d=7,loop=loop=71:size=1:start=72,setpts=N/24/TB",
           "-f", "lavfi", "-i", "anoisesrc=d=10:c=pink:a=0.02", *ENC, "-t", "10", str(d / "freeze.mkv"))
    ffmpeg("-f", "lavfi", "-i", f"testsrc2={SRC}:d=6", "-f", "lavfi", "-i",
           "aevalsrc=exprs='0.3*sin(2*PI*440*t)*(between(mod(t\\,1.37)\\,0\\,0.15)+between(mod(t\\,0.53)\\,0\\,0.05))'"
           ":s=48000:d=6", *ENC, str(d / "src.mkv"))
    for name, a_in in (("synced.mkv", 1.0), ("late.mkv", 0.88)):  # 0.88: the sound arrives 120 ms late
        ffmpeg("-i", str(d / "src.mkv"), "-filter_complex",
               f"testsrc2={SRC}:d=2,negate[pre];[0:v]trim=1:5,setpts=PTS-STARTPTS[shot];[pre][shot]concat=n=2:v=1:a=0[v];"
               f"anoisesrc=d=2:c=pink:a=0.005[apre];[0:a]atrim={a_in}:{a_in + 4},asetpts=PTS-STARTPTS[ashot];"
               "[apre][ashot]concat=n=2:v=0:a=1[a]", "-map", "[v]", "-map", "[a]", *ENC, str(d / name))
    (d / "shots.json").write_text(json.dumps([{"id": "S01", "source": "src.mkv", "in": 1.0, "out": 5.0, "at": 2.0}]))
    return d


def test_contract_metadata():
    tool = CutCheck()
    assert tool.name == "cut_check"
    assert tool.capability == "analysis"
    assert tool.input_schema["required"] == ["input_path"]
    assert set(tool.dependencies) == {"binary:ffmpeg", "binary:ffprobe"}
    assert tool.resource_profile.network_required is False
    assert tool.idempotency_key_fields == []  # a re-render at the same path must be checked again
    assert tool.estimate_cost({"input_path": "x.mp4"}) == 0.0


def test_registry_discovers_it():
    from tools.tool_registry import registry

    registry.discover("tools.analysis")
    tool = registry.get("cut_check")
    assert tool is not None and tool.capability == "analysis"


def test_status_follows_ffmpeg():
    with patch("tools.analysis.cut_check.shutil.which", return_value=None):
        assert CutCheck().get_status() == ToolStatus.UNAVAILABLE
    with patch("tools.analysis.cut_check.shutil.which", return_value="/usr/bin/ffmpeg"):
        assert CutCheck().get_status() == ToolStatus.AVAILABLE


def test_missing_file_is_an_error(tmp_path):
    result = CutCheck().execute({"input_path": str(tmp_path / "nope.mp4")})
    assert not result.success
    assert "not found" in result.error


def test_unknown_threshold_is_an_error(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    with patch("tools.analysis.cut_check.shutil.which", return_value="/usr/bin/ffmpeg"):
        result = CutCheck().execute({"input_path": str(video), "thresholds": {"nope": 1}})
    assert not result.success
    assert "Unknown threshold" in result.error


def test_timecode_and_drift():
    assert tc(64.72) == "1:04.72"
    x = [0.0] * 30 + [1.0, 2.0, 1.0] + [0.0] * 30
    assert drift_ms(x, x) == 0
    assert drift_ms(x, [0.0] * 12 + x[:-12]) == 120
    assert drift_ms([], x) is None


@needs_ffmpeg
def test_clean_cut_reports_nothing(media):
    result = CutCheck().execute({"input_path": str(media / "clean.mkv")})
    assert result.success, result.error
    assert result.data["findings"] == []
    assert result.data["cuts"] == 2


@needs_ffmpeg
def test_background_jump_at_both_cuts(media):
    found = kinds(CutCheck().execute({"input_path": str(media / "jump.mkv")}), "background")
    assert [round(f["t"]) for f in found] == [3, 6]
    found = kinds(CutCheck().execute({"input_path": str(media / "jump.mkv"),
                                      "thresholds": {"background_jump_db": 30}}), "background")
    assert found == []


@needs_ffmpeg
def test_click_loudness_and_true_peak(media):
    result = CutCheck().execute({"input_path": str(media / "click.mkv"), "target_lufs": -14})
    clicks = kinds(result, "click")
    assert len(clicks) == 1 and abs(clicks[0]["t"] - 5.0) < 0.02
    assert len(kinds(result, "loudness")) == 1
    assert len(kinds(result, "true_peak")) == 1  # 0.9 impulse is about -0.9 dBTP, over the default -1


@needs_ffmpeg
@pytest.mark.parametrize("name,kind,t", [("black.mkv", "black", 4.0), ("freeze.mkv", "freeze", 3.0)])
def test_black_and_freeze(media, name, kind, t):
    found = kinds(CutCheck().execute({"input_path": str(media / name)}), kind)
    assert len(found) == 1 and abs(found[0]["t"] - t) < 0.1


@needs_ffmpeg
def test_shot_sync(media):
    shots = str(media / "shots.json")
    assert kinds(CutCheck().execute({"input_path": str(media / "synced.mkv"), "shots": shots}), "sync") == []
    late = kinds(CutCheck().execute({"input_path": str(media / "late.mkv"), "shots": shots}), "sync")
    assert len(late) == 1 and "120 ms late" in late[0]["message"]


def test_thresholds_have_defaults():
    assert Thresholds().background_jump_db == 8.0
