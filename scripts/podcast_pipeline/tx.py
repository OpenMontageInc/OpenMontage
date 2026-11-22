"""Transcribe each session's reference-camera audio with MLX Whisper (word timestamps).
Usage: tx.py [--project project.json] [session ...]     (default: all sessions; skips ones already done)
Needs prep.py first (writes work/tx_<session>.wav). Output: work/tx_<session>.json - the clock every
other step uses."""
import sys, os, json, warnings
import cfg

warnings.filterwarnings("ignore")
PJ = cfg.get()
import mlx_whisper

want = [int(x) for x in sys.argv[1:] if x.isdigit()] or sorted(PJ.sessions)
for sid in want:
    out = PJ.tx_json(sid)
    if os.path.exists(out) and "--force" not in sys.argv: print("have", out); continue
    r = mlx_whisper.transcribe(PJ.tx_wav(sid), path_or_hf_repo=PJ.tx["model"], word_timestamps=True, language=PJ.tx["language"])
    json.dump(r, open(out, "w")); print("done", sid, flush=True)
