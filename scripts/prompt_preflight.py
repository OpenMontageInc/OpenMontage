"""Pre-take prompt preflight for multi-take narrative video (0 $, run before any paid take).

The take review (``scripts/raccord_sheet.py``) finds raccord defects after money is
spent. Most of them were already readable in the prompts: a trait repeated three
times pulls it into frame, a same-scene take without the previous take's last
frame re-stages the room, a plate with a child-size door shrinks every door.
This script checks every clip prompt of a scene plan, in story order, against
``skills/creative/shot-continuity.md`` and writes a report the agent must clear
before the first take.

Input: a JSON object ``{clip_id: clip}`` in story order (``scene_plan.metadata.clips``),
each clip with ``prompt``, ``images``, ``audio``, ``seconds`` and optionally ``cast``,
``speakers``, ``location``, ``mode`` (``reference`` | ``start_frame``), ``prev_frame``
(bool: the previous take's last frame is attached). Accepts a checkpoint_scene_plan.json too.

Checks — ERROR blocks the take, WARN must be answered in the report:
  structure  LOCATION MAP / CONTINUITY LOCKS / FINAL FRAME present; Shot ranges contiguous and within the duration
  refs       every @ImageN/@AudioN declared and attached; provider limits (images, audio files, audio seconds)
  sizing     shots / seconds / speakers per block (shot-continuity § 9)
  cast       people count stated; frame side stated for each character when 2+ are in the block
  phrasing   entries from off-screen, freezes, "appears" (models drop or pop them)
  traits     an asymmetric trait (--trait) repeated in a clip whose shots never show it
  mode       start_frame vs reference declared per clip; same location as the previous clip without its last frame
  handoff    FINAL FRAME states where each character stands (2+ cast)
  plates     every attached location plate listed in --plates-ok (scale checked: doors vs furniture vs people)
  handoff    table previous FINAL FRAME -> next opening, to read and confirm row by row

Usage:
    python scripts/prompt_preflight.py projects/<id>/checkpoint_scene_plan.json \\
        --trait scar --plate-glob '/L' --plates-ok projects/<id>/artifacts/plates_ok.json \\
        --out projects/<id>/artifacts/review/prompt_preflight.md
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

LIMITS = {"images": 9, "audio": 3, "audio_seconds": 15.0}
SHOT = re.compile(r"Shot (\d+) \((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\s*s\)", re.I)
RISKY = [r"\benters?\b", r"\bwalks in\b", r"from off-?screen", r"\bappears?\b", r"\bstands? still\b", r"\bfreez", r"\bpops? (in|up)\b"]
SIDE = re.compile(r"frame (left|right)", re.I)


def load_clips(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "artifacts" in data:  # checkpoint_scene_plan.json
        data = data["artifacts"]["scene_plan"]["metadata"]["clips"]
    return data


def audio_seconds(paths: list[str]) -> float | None:
    total = 0.0
    for p in paths:
        try:
            total += float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                          capture_output=True, text=True, check=True).stdout)
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
            return None
    return total


def section(prompt: str, name: str) -> str:
    m = re.search(rf"{name}:\s*(.+)", prompt)
    return m.group(1).strip() if m else ""


def check_clip(cid: str, c: dict, prev: tuple[str, dict] | None, a) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    p, sec = c["prompt"], float(c.get("seconds") or 0)

    def add(level, check, msg):
        out.append((level, check, msg))

    for block in ("LOCATION MAP", "CONTINUITY LOCKS", "FINAL FRAME"):
        if block not in p:
            add("ERROR", "structure", f"missing {block}")
    shots = [(int(n), float(s), float(e)) for n, s, e in SHOT.findall(p)]
    if not shots:
        add("ERROR", "structure", "no 'Shot N (a-bs)' lines")
    else:
        if shots[0][1] != 0:
            add("WARN", "structure", f"Shot 1 starts at {shots[0][1]}s")
        for (n1, _, e1), (n2, s2, _) in zip(shots, shots[1:]):
            if abs(e1 - s2) > 0.01:
                add("ERROR", "structure", f"gap/overlap between shot {n1} (ends {e1}) and shot {n2} (starts {s2})")
        if sec and abs(shots[-1][2] - sec) > 0.51:
            add("WARN", "structure", f"last shot ends at {shots[-1][2]}s, take is {sec:g}s")

    imgs, auds = list(c.get("images") or []), c.get("audio") or []
    if c.get("prev_take"):  # attached at take time by the runner (frame of a take that may not exist yet)
        imgs.append("<previous take frame>")
    for kind, attached in (("Image", imgs), ("Audio", auds)):
        used = sorted({int(x) for x in re.findall(rf"@{kind}(\d+)", p)})
        if used and max(used) > len(attached):
            add("ERROR", "refs", f"@{kind}{max(used)} cited but only {len(attached)} attached")
        for i in range(1, len(attached) + 1):
            if i not in used:
                add("WARN", "refs", f"@{kind}{i} attached but never cited ({Path(attached[i - 1]).name})")
    if len(imgs) > LIMITS["images"]:
        add("ERROR", "refs", f"{len(imgs)} images > {LIMITS['images']}")
    if len(auds) > LIMITS["audio"]:
        add("ERROR", "refs", f"{len(auds)} audio files > {LIMITS['audio']}")
    if auds and not a.no_probe:
        s = audio_seconds(auds)
        if s is not None and s > LIMITS["audio_seconds"]:
            add("ERROR", "refs", f"audio references total {s:.1f}s > {LIMITS['audio_seconds']:g}s")
    for f in imgs + auds:
        if f.startswith("<"):
            continue
        if not Path(f).exists():
            add("ERROR", "refs", f"missing file {f}")

    cast, speakers = c.get("cast") or [], c.get("speakers") or []
    n = len(shots)
    mixed = len(cast) > 1
    if (mixed and (n > 3 or sec > 12)) or (n > 4 or sec > 15):
        add("ERROR", "sizing", f"{n} shots / {sec:g}s with cast {cast} (max 3/12s mixed, 4/15s same cast)")
    if len(speakers) > 2:
        add("ERROR", "sizing", f"{len(speakers)} speakers > 2")
    if speakers and len(auds) < len(speakers):
        add("WARN", "refs", f"{len(speakers)} speakers but {len(auds)} voice references")

    if not re.search(r"exactly (one|two|three|four|\d+) (person|people)|one person|nobody|no one", p, re.I):
        add("WARN", "cast", "people count not stated ('Exactly two people in frame')")
    if len(cast) >= 2 and len(SIDE.findall(p)) < 2:
        add("WARN", "cast", "2+ characters but frame sides not stated for each")

    body = p.split("CONTINUITY LOCKS")[0]
    for pat in RISKY:
        for m in re.finditer(pat, body, re.I):
            ctx = body[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
            add("WARN", "phrasing", f"'{m.group(0)}' — entries/freezes/pop-ins fail (§3): …{ctx}…")

    for trait in a.trait or []:
        total = len(re.findall(trait, p, re.I))
        in_shots = sum(len(re.findall(trait, ln, re.I)) for ln in p.splitlines() if SHOT.search(ln))
        if total >= 2 and in_shots == 0:
            add("WARN", "traits", f"'{trait}' cited {total}x but no shot shows it: it migrates into frame (face, neck). Drop it from the locks here")
        elif total >= 3:
            add("WARN", "traits", f"'{trait}' cited {total}x: keep it in the shot that shows it only")

    final = section(p, "FINAL FRAME")
    if len(cast) >= 2 and final and not re.search(r"frame (left|right)|\b(at|by|near|beside|next to|in the|on the|against)\b", final, re.I):
        add("WARN", "handoff", f"FINAL FRAME '{final[:80]}' names no position: the next take can't know where each character stands")

    mode = c.get("mode")
    if mode not in ("reference", "start_frame"):
        add("WARN", "mode", "mode not declared (reference | start_frame), see shot-continuity § 6 table")
    if prev and c.get("location") and c.get("location") == prev[1].get("location") and mode != "start_frame" and not (c.get("prev_frame") or c.get("prev_take")):
        add("WARN", "mode", f"same location as {prev[0]}: attach {prev[0]}'s real last frame as the LAST reference (or justify: reverse/insert/ECU)")

    if a.plate_glob:
        ok = set(json.loads(Path(a.plates_ok).read_text())) if a.plates_ok and Path(a.plates_ok).exists() else set()
        for f in imgs:
            if any(g in f for g in a.plate_glob) and Path(f).name not in ok:
                add("ERROR", "plates", f"plate {Path(f).name} not scale-checked (doors vs furniture vs people); add it to --plates-ok once checked")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--trait", action="append", help="asymmetric trait word to watch (repeatable), e.g. scar")
    ap.add_argument("--plate-glob", action="append", help="substring identifying location plates in image paths (repeatable), e.g. '/refs/L'")
    ap.add_argument("--plates-ok", help="JSON list of plate file names whose scale was checked")
    ap.add_argument("--no-probe", action="store_true", help="skip ffprobe of audio references")
    a = ap.parse_args(argv)
    clips = load_clips(a.clips)
    lines, n_err, prev, handoff = ["# Prompt preflight", ""], 0, None, []
    for cid, c in clips.items():
        res = check_clip(cid, c, prev, a)
        n_err += sum(1 for r in res if r[0] == "ERROR")
        lines.append(f"## {cid} — {sum(r[0] == 'ERROR' for r in res)} error(s), {sum(r[0] == 'WARN' for r in res)} warning(s)")
        lines += [f"- **{lvl}** [{chk}] {msg}" for lvl, chk, msg in res] or ["- ok"]
        lines.append("")
        if prev:
            first = next((ln for ln in c["prompt"].splitlines() if SHOT.search(ln)), "")
            handoff.append(f"| {prev[0]} → {cid} | {section(prev[1]['prompt'], 'FINAL FRAME')[:160]} | {first[:160]} | |")
        prev = (cid, c)
    lines += ["## Handoff (read each row: does the opening follow from the previous final frame? positions, who is where, state)", "",
              "| cut | previous FINAL FRAME | next opening | verdict |", "|---|---|---|---|", *handoff, ""]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(lines), encoding="utf-8")
    print(a.out, f"{n_err} error(s)")
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
