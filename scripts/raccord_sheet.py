"""Build raccord (continuity) review sheets for a sequence of generated takes.

A take review that only looks at a 1 fps strip of each take misses the defects
that live *between* shots: a character who moved between two blocks, a reverse
shot showing the wrong wall, a door that shrank. This script lays out, for every
take in story order, the panels a script supervisor actually compares:

    [last frame of the previous take] | shot 1 first | shot 1 last | shot 2 first | ...

Shots come from ffmpeg scene-cut detection (``scdet``); cuts closer than
``--min-shot`` seconds are treated as flicker and merged. Sheets with many shots
are split so each image stays legible for a vision review (≤ ``--max-panels``).

It also writes ``raccord_checklist.md``: one row per cut to fill in with the
verdict for each check of ``skills/creative/shot-continuity.md`` §8 (positions,
sides, background vs. plate, proportions, states, asymmetric traits). The review
is done only when every row has a verdict.

Usage:
    python scripts/raccord_sheet.py --out projects/<id>/artifacts/review/raccord \\
        projects/<id>/assets/video/B1_t1.mp4 projects/<id>/assets/video/B2_t1.mp4 ...
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CHECKS = ["positions/sides", "background vs plate", "proportions", "states (hood, wet, props)", "traits (scar side)", "eyelines"]


def probe_duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def detect_cuts(video: Path, threshold: float = 10.0) -> list[float]:
    err = subprocess.run(["ffmpeg", "-i", str(video), "-vf", f"scdet=threshold={threshold},metadata=print:key=lavfi.scd.time",
                          "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    return [float(x) for x in re.findall(r"lavfi.scd.time=([0-9.]+)", err)]


def shots_from_cuts(cuts: list[float], duration: float, min_shot: float) -> list[tuple[float, float]]:
    bounds = [0.0]
    for c in sorted(cuts):
        if c - bounds[-1] >= min_shot and duration - c >= min_shot:
            bounds.append(c)
    bounds.append(duration)
    return list(zip(bounds, bounds[1:]))


def grab(video: Path, t: float, width: int, cache: Path) -> Image.Image:
    p = cache / f"{video.stem}_{t:.2f}.png"
    if not p.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(t, 0):.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={width}:-2", str(p)], check=True)
    return Image.open(p)


def sheet(panels: list[tuple[str, Image.Image]], path: Path, font) -> None:
    w, h = panels[0][1].size
    img = Image.new("RGB", (w * len(panels) + 6 * (len(panels) - 1), h + 30), "white")
    d = ImageDraw.Draw(img)
    for k, (label, im) in enumerate(panels):
        img.paste(im, (k * (w + 6), 30))
        d.text((k * (w + 6) + 4, 4), label, fill="red" if label.endswith("END") else "black", font=font)
    img.save(path, quality=88)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("takes", nargs="+", type=Path, help="retained takes in story order")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--width", type=int, default=300)
    ap.add_argument("--min-shot", type=float, default=0.25)
    ap.add_argument("--max-panels", type=int, default=7)
    a = ap.parse_args(argv)
    cache = a.out / "_frames"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    rows, prev = [], None
    for take in a.takes:
        dur = probe_duration(take)
        shots = shots_from_cuts(detect_cuts(take), dur, a.min_shot)
        panels = []
        if prev is not None:
            panels.append((f"{prev[0].stem} END", grab(prev[0], prev[1] - 0.3, a.width, cache)))
            rows.append(f"| {prev[0].stem} → {take.stem} (between takes) |" + " |" * len(CHECKS))
        for n, (s, e) in enumerate(shots, 1):
            panels.append((f"{take.stem} s{n} {s:.1f}", grab(take, s + 0.12, a.width, cache)))
            panels.append((f"{take.stem} s{n} {e:.1f}", grab(take, e - (0.3 if n == len(shots) else 0.12), a.width, cache)))
            if n > 1:
                rows.append(f"| {take.stem} s{n - 1} → s{n} |" + " |" * len(CHECKS))
        chunks = [panels[i:i + a.max_panels] for i in range(0, len(panels), a.max_panels)]
        for i, chunk in enumerate(chunks):
            sheet(chunk, a.out / (f"{take.stem}.jpg" if len(chunks) == 1 else f"{take.stem}_{i + 1}.jpg"), font)
        print(take.stem, f"{dur:.1f}s", len(shots), "shots", flush=True)
        prev = (take, dur)
    head = "| cut | " + " | ".join(CHECKS) + " |\n|" + "---|" * (len(CHECKS) + 1) + "\n"
    (a.out / "raccord_checklist.md").write_text(
        "# Raccord checklist\n\nFill every cell: ok / defect (what, major or minor). Also note each location plate's scale "
        "(doors vs furniture vs people) once, at the top.\n\n" + head + "\n".join(rows) + "\n", encoding="utf-8")
    print(a.out / "raccord_checklist.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
