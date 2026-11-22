# Podcast Editing Pipeline

Turns a multi-camera podcast shoot (guest camera, host/co-host two-shot camera, wide camera, multitrack
recorder) into ~75 s vertical reels, horizontal versions, multicam proxies, a team review room, **full-length
16x9 episodes**, and Final Cut Pro 13 timelines for both. Every show-specific value lives in one `project.json`;
`examples/three-camera-show/` is a filled-in example (A7S III two-shot + FX3 guest + Osmo Pocket 3 wide,
RODECaster multitrack, two sessions).

| Script | Does |
|---|---|
| `setup.sh project.json` | venv (`~/.venvs/podcast-editing-pipeline`), compiles `tools/personmask.swift`, runs `prep.py` |
| `prep.py` | extracts transcription wavs + 1 kHz sync audio + mic tracks into `work/` |
| `sync.py project.json` | **finds all camera/recorder offsets by audio cross-correlation**, assigns files to sessions, writes project.json |
| `tx.py` | MLX Whisper large-v3-turbo transcripts with word timestamps (`work/tx_<session>.json`) |
| `picks.py` | `picks.json` (topic + first/last words) -> `clips.json` (exact ranges) |
| `render3.py clips.json --fmt v,h,q [name]` | renders vertical / horizontal / quad proxy; writes `shots/<name>.json` |
| `run_all.sh project.json` | renders every reel, 3 in parallel |
| `audit_headroom.py out.json *.mp4` | flags heads touching the top edge |
| `gen_room.py` | builds `review-room.html` (publish as a claude.ai Artifact) |
| `render_hsplits.py` -> `export_fcpxml.py` -> `restructure_fcpxml.py` | Final Cut Pro 13 XML (reels) |
| `episode.py [episodes.json] plan / render / fcpxml` | full-length 16x9 episodes: auto multicam edit, MP4, FCP 13 XML + SRT |

All scripts take `--project path/to/project.json` (or run them from the show folder). Use the venv's python:
`~/.venvs/podcast-editing-pipeline/bin/python`.

## New-show checklist

1. **Make a show folder** outside the footage folders, e.g. `<shoot>/REELS/_pipeline/`, and copy
   `examples/three-camera-show/project.json` into it. Keep footage untouched.
2. **Fill in project.json**
   - `show.name`, `show.slug` (no spaces), `show.subtitle`
   - `footage_root` (the shoot folder) and `out_root` / `outputs` (where renders + FCPXML go)
   - `cache` and `venv`: `~` paths with **no `:`** (shoot folders like `NAME_11:19` break ffmpeg + venvs)
   - `lut`: a plain `.cube` (Resolve's `.cube_shaperlut` packs must be exported to `.cube` first).
     No LUT ships with this folder (camera/creative LUTs are usually licensed) - point `lut` at your own,
     e.g. a Sony S-Log3/S-Gamut3.Cine -> Rec.709 `.cube`.
   - `timeline_fps` and `source_resolution` (check with `ffprobe`)
   - `cameras`: one entry each. `role` = `two_shot` | `guest` | `wide`; exactly one `"reference": true`
     (its audio is transcribed - pick the camera with the cleanest audio that runs the whole session);
     `rate`; `log` + `grade: "lut"` for log footage, or `grade: "filter"` + an ffmpeg `filter` that matches
     a non-log camera to the graded ones; `timecode`: `"ignore"` for Sony XAVC, `"tmcd"` for cameras with a real
     timecode track (DJI); `files`: a glob (relative to footage_root) of that camera's clips; wide only:
     `vertical_crop` [x, y] of the 1080x1920 vertical window.
   - `recorder`: `files` glob for the mix files (e.g. `AUDIO/**/Stereo Mix.wav`), `mix` filename.
   - `people` `guest` / `host` / `cohost`: on-screen `label`, `camera`, `face_x`/`face_y` (face centre in source
     px - grab a frame and measure), `mic` (the recorder track file for that person's mic; verify by listening).
     `face_x_by_session` overrides face_x when the guest sat differently in a session.
   - `sessions`: one per recorded segment: `id`, `guest` (name shown in the review room), `ref_file`
     (the reference camera's file; exactly one per session). Leave `media`/`recorder` out - sync fills them.
   - `transcript_fixes`: names/jargon Whisper gets wrong (fill after reading the transcripts).
   - `fonts`, `brand` colors (review room + title ink), `fcp.event`, `fcp.fcpxml_name`, `fcp.lut_keyword`.
3. `./setup.sh <show>/project.json`
4. `python sync.py <show>/project.json` - check the table: every file assigned, `match` well above 8
   (a clean three-camera shoot scored 25-105), drift in tens of ms. Unmatched files (test takes) are listed and skipped.
5. `python tx.py --project <show>/project.json`
6. Write `picks.json` (Claude can read the transcripts and propose topics), then `python picks.py`.
7. Render **one** reel, review it, then `run_all.sh`.
8. `audit_headroom.py` on the outputs; fix and re-render flagged reels.
9. Upload proxies, `gen_room.py`, publish the review room; apply team edits/looks and re-render.
10. FCP: `render_hsplits.py`, `export_fcpxml.py`, `restructure_fcpxml.py` - export one reel first and let the
    user import it into a **scratch** library (never test-import into client libraries yourself).

## Full-length episodes

`episode.py` cuts whole episodes from the same show setup (sync, transcripts, mic tracks, framing, grade).
Write `episodes.json` in the show folder (example: `examples/three-camera-show/episodes.json`):

```json
[{"name": "01_guest_1", "session": 1, "title": "Show - Guest 1",
  "keep": [[236.95, 3633.75]], "max_gap": 2.5,
  "demo": [[3162.0, 3345.0]]}]
```

- `keep`: in/out ranges on the reference clock. Read the transcript to find the real top of the show and the
  sign-off; split around breaks ("pause", mic changes) and any gap with no recorder audio.
- `max_gap`: silences longer than this are squeezed to ~1 s (light trim). Content is otherwise untouched.
- `demo` (optional): stretches where people leave their marks (a product demo); those cut wide / guest camera only.

**Topic episodes** use the same format: one entry per topic (5-8 min each works well for YouTube), each `keep`
opening on the question that starts the topic. Several ranges per entry cut out retakes ("sorry, let me start
over") or off-record asides. Keep them in a separate file (e.g. `topics.json`) and pass it as the first argument
(`episode.py --project P topics.json plan`). Extra keys (e.g. `"reel": "07_slug"` to pair a topic with its
sub-90 s vertical reel from `clips.json`) are ignored.

Then, with the venv's python:

1. `episode.py --project P plan` - auto edit -> `shots/ep_<name>.json`; prints shot counts per angle.
2. `episode.py --project P render <name> --test 75` - check grade, framing and sync on the first 75 s.
3. `episode.py --project P render` - every episode -> `<footage_root>/<episodes_root>/<name>.mp4`
   (`episodes_root` defaults to `EPISODES`). ~1.3x real time per episode on an M-series Mac with 4K
   4:2:2 10-bit sources; temp files live in the cache and are deleted per episode.
4. `episode.py --project P fcpxml` - one FCP 13 event with every episode on the original camera files + recorder
   mix (same cuts/framing as the MP4), 16x9 splits pre-framed into `FCP MEDIA/<name>/`, plus `captions/<name>.srt`.

Long-form editing on top of the reels' speaker-following cuts: guest answers rotate close-up / punch-in /
two-shot reaction / wide every 5-12 s; long host turns cut away to the guest listening; 6 s wide opener.
Over an hour the clocks drift, so single-file cameras re-sync every 120 s and the recorder every 300 s.
Audio: recorder stereo mix, sample-exact to the frame grid, two-pass loudnorm to `episode_loudness` (default -16 LUFS).

## Show folder layout (created as you go)

```
project.json  picks.json  clips.json  look.json  asset_urls.json  review-room.html
episodes.json
edits/<reel>.json   shots/<reel>.json   shots/ep_<episode>.json   work/{tx_*.json, tx_*.wav, env/*.raw, fcpxml_gap_version.fcpxml}
```

## Limits

- Three people (guest, host, cohost) + one wide angle; the review room's multicam quad assumes this.
- Framing sizes are tuned for 3840x2160 sources; check the first reel if your cameras differ.
- The reference camera must be a single continuous file per session (other cameras/recorders may split).
- Rendering needs macOS (VideoToolbox ProRes, Apple Vision person mask, MLX Whisper on Apple silicon).
