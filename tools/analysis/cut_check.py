"""Measured QC of a finished cut: what you hear or see in an edit that a probe does not show.

The final review in ``video_compose`` probes the container, reads whole-file mean and max volume and looks at
four sampled frames. This tool measures the full timeline instead and returns timestamped findings:

- background level jumps at a cut (the room tone of one shot much louder or quieter than the next)
- clicks (an abrupt jump in the waveform, typical of a splice without a fade)
- silences in the middle of the piece
- black stretches and frozen picture anywhere in the timeline, not only at sampled frames
- per-shot audio drift against the source clip, when a shot list is given
- integrated loudness and true peak against a delivery target

It reports and does not decide: some jumps and black frames are intentional (a hit on black, an end card), so
the agent listens or looks at each timestamp. Needs ffmpeg/ffprobe only; Python standard library otherwise.
Defaults were tuned on vertical 1080x1920, 24 fps dialogue-over-ambience episodes cut from AI-generated clips.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

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


@dataclass
class Thresholds:
    """Defaults tuned on real AI-generated short drama episodes: vertical 1080x1920, 24 fps, dialogue over
    ambience, cut from many generated clips. Every value can be changed."""

    background_jump_db: float = 8.0   # background level difference either side of a cut
    background_floor_db: float = -55.0  # below this the background counts as silence and is not compared
    click_crest: float = 20.0         # in a 10 ms window: largest sample-to-sample jump over the mean jump
    click_min: float = 0.08           # smallest sample-to-sample jump worth reporting (full scale is 0 to 2)
    click_merge_s: float = 0.6        # clicks closer than this are reported as one zone
    silence_db: float = -55.0         # below this, silence
    silence_min_s: float = 0.30       # shortest silence worth reporting
    black_min_s: float = 0.12         # shortest black stretch worth reporting
    freeze_min_s: float = 2.0         # shortest still picture worth reporting
    scene_threshold: float = 0.10     # ffmpeg scene score that counts as a cut
    sync_tolerance_ms: int = 40       # one frame at 24 fps is 42 ms
    end_margin_s: float = 1.5         # the ending (fade to black, end card) is not checked for black or silence


def tc(t: float) -> str:
    """Seconds to m:ss.ss."""
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m)}:{s:05.2f}"


def _ffmpeg(args: list[str], cwd: str | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", *args], capture_output=True, text=True,
                          cwd=cwd, timeout=timeout)


def probe(path: str) -> dict:
    """Duration, picture size, frame rate and audio channels, from ffprobe."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate,channels:format=duration",
                        "-of", "json", path], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe could not read the file: {r.stderr.strip()[:200]}")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    info = {"duration": float((data.get("format") or {}).get("duration") or 0.0),
            "width": None, "height": None, "fps": None,
            "audio_channels": int(audio.get("channels") or 0) if audio else 0}
    if video:
        info["width"], info["height"] = video.get("width"), video.get("height")
        num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
        if float(den or 1):
            info["fps"] = round(float(num) / float(den or 1), 3)
    return info


def picture_pass(path: str, th: Thresholds) -> tuple[list[float], list[tuple], list[tuple]]:
    """One pass over a small copy of the picture: cuts, black stretches and frozen stretches."""
    r = _ffmpeg(["-i", path, "-an", "-vf",
                 f"scale=270:-2,blackdetect=d={th.black_min_s}:pix_th=0.10,"
                 f"freezedetect=n=0.003:d={th.freeze_min_s},"
                 f"select='gt(scene,{th.scene_threshold})',showinfo", "-f", "null", "-"])
    e = r.stderr
    cuts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", e)]
    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", e)]
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", e)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", e)]
    freezes = list(zip(starts, ends + [None] * (len(starts) - len(ends))))
    return cuts, blacks, freezes


def audio_pass(path: str, th: Thresholds, channels: int) -> tuple[list[tuple], list[tuple], float | None, float | None]:
    """One pass over the sound. Every 10 ms: RMS level and the largest and mean sample-to-sample jump of the
    mono mix. Also the silences, the integrated loudness and the true peak."""
    downmix = "anull" if channels <= 1 else "pan=mono|c0=0.5*c0+0.5*c1"
    graph = (f"[0:a:0]asplit=2[m][l];[l]ebur128=peak=true[lo];"
             f"[m]aresample=48000,{downmix},aformat=sample_fmts=flt,"
             f"silencedetect=n={th.silence_db}dB:d={th.silence_min_s},asetnsamples=480,"
             f"astats=metadata=1:reset=1,ametadata=mode=print:file=astats.txt,anullsink")
    with tempfile.TemporaryDirectory(prefix="cutcheck_") as tmp:
        r = _ffmpeg(["-nostats", "-i", os.path.abspath(path), "-filter_complex", graph, "-map", "[lo]",
                     "-f", "null", "-"], cwd=tmp)
        try:
            txt = Path(tmp, "astats.txt").read_text(encoding="utf-8", errors="replace")
        except OSError:
            txt = ""
    if r.returncode != 0 and not txt:
        raise RuntimeError(f"ffmpeg could not analyse the sound: {r.stderr.strip()[-200:]}")

    rows, t, cur = [], None, {}

    def flush():
        if cur and t is not None:
            rows.append((t, cur.get("RMS_level", -120.0), cur.get("Max_difference", 0.0),
                         cur.get("Mean_difference", 0.0)))

    for line in txt.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            flush()
            t, cur = float(m.group(1)), {}
            continue
        m = re.match(r"lavfi\.astats\.Overall\.(\w+)=(.+)", line)
        if m:
            try:
                cur[m.group(1)] = float(m.group(2))
            except ValueError:  # "-inf" on digital silence
                cur[m.group(1)] = -120.0
    flush()

    e = r.stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", e)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", e)]
    silences = list(zip(starts, ends + [None] * (len(starts) - len(ends))))
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", e)
    peak = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", e)
    loudness = float(lufs[-1]) if lufs else None
    true_peak = float(peak[-1]) if peak and peak[-1] != "-inf" else None
    return rows, silences, loudness, true_peak


def background(rows: list[tuple], a: float, b: float) -> float | None:
    """Background level in [a, b): the 10th percentile of the 10 ms RMS, which falls between words."""
    v = sorted(x[1] for x in rows if a <= x[0] < b and x[1] > -119)
    if len(v) < 20:
        return None
    return v[int(len(v) * 0.1)]


def envelope(path: str, a: float, b: float, sr: int = 8000, win: int = 80) -> list[float]:
    """RMS envelope every 10 ms of [a, b], mean removed."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", path,
                        "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True, timeout=300)
    n = len(r.stdout) // 2
    if n < win * 10:
        return []
    s = struct.unpack(f"<{n}h", r.stdout[:n * 2])
    env = [math.sqrt(sum(x * x for x in s[k:k + win]) / win) for k in range(0, n - win, win)]
    mean = sum(env) / len(env)
    return [x - mean for x in env]


def drift_ms(source: list[float], edit: list[float], max_lag: int = 25) -> int | None:
    """Lag (in ms, 10 ms steps, up to +-250 ms) that best lines the edit up with the source.
    Positive means the sound in the edit comes later than in the source."""
    if not source or not edit:
        return None
    best, lag = None, 0
    for k in range(-max_lag, max_lag + 1):
        total = sum(source[i] * edit[i + k] for i in range(len(source)) if 0 <= i + k < len(edit))
        if best is None or total > best:
            best, lag = total, k
    return lag * 10


def load_shots(shots: str | list | None) -> tuple[list[dict], Path | None]:
    """A shot list is a JSON list (or the path to one) of
    {"id": "S01", "source": "clip.mp4", "in": 1.7, "out": 4.3, "at": 1.667}:
    `in` and `out` in seconds of the source clip, `at` where the shot starts in the edit. Relative `source`
    paths are read from the shot list's folder. List only shots that play their own sound."""
    if not shots:
        return [], None
    if isinstance(shots, (str, os.PathLike)):
        p = Path(shots)
        return json.loads(p.read_text(encoding="utf-8")), p.parent
    return list(shots), None


def _finding(t: float, kind: str, message: str) -> dict:
    return {"t": round(t, 2), "kind": kind, "message": message}


def _click(zone: list) -> dict:
    a, b, n = zone
    if n == 1:
        return _finding(a, "click", "possible click (abrupt jump in the waveform)")
    return _finding(a, "click", f"{n} possible clicks in a row, until {tc(b)}")


def check(path: str, shots: str | list | None = None, target_lufs: float | None = None,
          lufs_tolerance: float = 1.0, max_true_peak: float | None = -1.0,
          thresholds: Thresholds | None = None) -> dict:
    """Check one video. Returns a summary plus a list of findings, each with a timestamp."""
    th = thresholds or Thresholds()
    info = probe(path)
    dur = info["duration"]
    report = {"file": path, **info, "cuts": 0, "loudness_lufs": None, "true_peak_dbtp": None, "findings": []}
    found = report["findings"]
    if dur <= 0:
        found.append(_finding(0, "error", "could not read the duration"))
        return report
    ending = dur - th.end_margin_s

    if info["width"]:
        cuts, blacks, freezes = picture_pass(path, th)
        report["cuts"] = len(cuts)
        for a, b in blacks:
            if a < ending and b - a >= th.black_min_s:
                found.append(_finding(a, "black", f"black picture for {b - a:.2f} s"))
        for a, b in freezes:
            b = dur if b is None else b
            if a < ending:
                found.append(_finding(a, "freeze", f"frozen picture for {b - a:.1f} s"))
    else:
        cuts = []
        found.append(_finding(0, "error", "no video stream"))

    if not info["audio_channels"]:
        found.append(_finding(0, "no_audio", "no audio stream"))
        found.sort(key=lambda f: f["t"])
        return report

    rows, silences, loudness, true_peak = audio_pass(path, th, info["audio_channels"])
    report["loudness_lufs"], report["true_peak_dbtp"] = loudness, true_peak

    for c in cuts:
        if c < 1.0 or c > dur - 1.0:
            continue
        before, after = background(rows, c - 1.3, c - 0.1), background(rows, c + 0.1, c + 1.3)
        if before is None or after is None or max(before, after) < th.background_floor_db:
            continue
        jump = after - before
        if abs(jump) >= th.background_jump_db:
            way = "up" if jump > 0 else "down"
            found.append(_finding(c, "background", f"background level goes {way} {abs(jump):.0f} dB at the cut"))

    zone = None
    for t, _rms, max_d, mean_d in rows:
        if max_d < th.click_min or mean_d <= 0 or max_d / mean_d < th.click_crest:
            continue
        if zone and t - zone[1] <= th.click_merge_s:
            zone[1], zone[2] = t, zone[2] + 1
        else:
            if zone:
                found.append(_click(zone))
            zone = [t, t, 1]
    if zone:
        found.append(_click(zone))

    for a, b in silences:
        b = dur if b is None else b
        if 0.3 < a < ending and b < dur - 0.2:
            found.append(_finding(a, "silence", f"silence for {b - a:.2f} s"))

    shot_list, base = load_shots(shots)
    for s in shot_list:
        try:
            src, t_in, t_out, at = s["source"], float(s["in"]), float(s["out"]), float(s["at"])
        except (KeyError, TypeError, ValueError):
            found.append(_finding(0, "error", f"shot list entry needs source, in, out and at: {s}"))
            continue
        if base is not None and not os.path.isabs(src):
            src = str(base / src)
        length = t_out - t_in
        if length < 1.2:
            continue
        d = drift_ms(envelope(src, t_in + 0.3, t_out - 0.3), envelope(path, at + 0.3, at + length - 0.3))
        if d is not None and abs(d) >= th.sync_tolerance_ms:
            way = "late" if d > 0 else "early"
            found.append(_finding(at, "sync", f"shot {s.get('id', '?')} sound is {abs(d)} ms {way} against its source"))

    if target_lufs is not None and loudness is not None and abs(loudness - target_lufs) > lufs_tolerance:
        found.append(_finding(0, "loudness", f"integrated loudness {loudness:.1f} LUFS "
                                             f"(target {target_lufs:g} +-{lufs_tolerance:g})"))
    if max_true_peak is not None and true_peak is not None and true_peak > max_true_peak:
        found.append(_finding(0, "true_peak", f"true peak {true_peak:.1f} dBTP (max {max_true_peak:g})"))

    found.sort(key=lambda f: f["t"])
    return report


class CutCheck(BaseTool):
    name = "cut_check"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "analysis"
    provider = "ffmpeg"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["binary:ffmpeg", "binary:ffprobe"]
    install_instructions = (
        "Install ffmpeg (includes ffprobe):\n"
        "  Windows: winget install ffmpeg\n"
        "  macOS: brew install ffmpeg\n"
        "  Linux: sudo apt install ffmpeg"
    )
    agent_skills = ["ffmpeg"]

    capabilities = [
        "background_jump_detection",
        "click_detection",
        "silence_detection",
        "black_frame_detection",
        "freeze_detection",
        "shot_sync_check",
        "loudness_check",
    ]
    best_for = [
        "checking a finished cut before final_review is written",
        "edits assembled from many generated clips, each with its own room tone",
        "finding the exact timestamps of clicks, background jumps, black or frozen picture",
        "verifying that each shot's sound still lines up with its source clip",
    ]
    not_good_for = [
        "judging creative quality, pacing or whether a jump is intentional (the agent decides)",
        "soft cross-dissolves, which scene detection may not register as cuts",
        "fixing problems: it only reports them",
    ]

    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string", "description": "Rendered video to check"},
            "shots": {
                "description": (
                    "Optional shot list for the sync check: a list, or the path to a JSON list, of "
                    "{id, source, in, out, at}. in/out are seconds of the source clip, at is where the shot "
                    "starts in the edit. Relative sources resolve against the JSON file's folder. "
                    "List only shots that play their own sound."
                ),
                "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "object"}}],
            },
            "target_lufs": {"type": "number", "description": "Delivery loudness, e.g. -14. Omit to skip."},
            "lufs_tolerance": {"type": "number", "default": 1.0},
            "max_true_peak_dbtp": {"type": "number", "default": -1.0},
            "thresholds": {
                "type": "object",
                "description": "Overrides for detection thresholds, by name (see Thresholds).",
            },
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "file": {"type": "string"},
            "duration": {"type": "number"},
            "width": {"type": ["integer", "null"]},
            "height": {"type": ["integer", "null"]},
            "fps": {"type": ["number", "null"]},
            "cuts": {"type": "integer"},
            "loudness_lufs": {"type": ["number", "null"]},
            "true_peak_dbtp": {"type": ["number", "null"]},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "t": {"type": "number"},
                        "kind": {
                            "type": "string",
                            "enum": ["background", "click", "silence", "black", "freeze", "sync",
                                     "loudness", "true_peak", "no_audio", "error"],
                        },
                        "message": {"type": "string"},
                    },
                },
            },
        },
    }

    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=256, vram_mb=0, disk_mb=0, network_required=False)
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    # No idempotency fields on purpose: a re-render keeps the same path, and the check must run again.
    idempotency_key_fields: list[str] = []
    side_effects: list[str] = []
    user_visible_verification = [
        "Listen to or look at the rendered video at each finding's timestamp before deciding it is a defect",
    ]

    def get_status(self) -> ToolStatus:
        if shutil.which("ffmpeg") and shutil.which("ffprobe"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        # About 5 to 10 s per minute of 1080p video on a laptop; the duration is not known before probing.
        return 15.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        path = Path(inputs["input_path"])
        if not path.exists():
            return ToolResult(success=False, error=f"File not found: {path}")
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(success=False, error="ffmpeg and ffprobe are needed on PATH")

        th = Thresholds()
        known = {f.name for f in fields(Thresholds)}
        for key, value in (inputs.get("thresholds") or {}).items():
            if key not in known:
                return ToolResult(success=False, error=f"Unknown threshold {key!r}; known: {sorted(known)}")
            setattr(th, key, type(getattr(th, key))(value))

        start = time.time()
        try:
            report = check(
                str(path),
                shots=inputs.get("shots"),
                target_lufs=inputs.get("target_lufs"),
                lufs_tolerance=float(inputs.get("lufs_tolerance", 1.0)),
                max_true_peak=inputs.get("max_true_peak_dbtp", -1.0),
                thresholds=th,
            )
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            return ToolResult(success=False, error=f"cut_check failed: {e}")
        return ToolResult(success=True, data=report, duration_seconds=round(time.time() - start, 2))
