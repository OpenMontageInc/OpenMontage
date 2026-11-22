#!/bin/zsh
# One-time machine setup + per-show audio prep.
# Usage: setup.sh path/to/project.json
#   - creates the shared venv (mlx-whisper, pillow, opencv, numpy) outside any ':' path
#   - compiles the Apple Vision person-mask CLI (tools/personmask.swift)
#   - extracts transcription audio + sync envelopes (prep.py)
set -e
T="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
[ -f "$PROJ" ] || { echo "usage: setup.sh project.json"; exit 1; }
VE=$(/usr/bin/python3 -c "import json,os,sys;print(os.path.expanduser(json.load(open(sys.argv[1])).get('venv','~/.venvs/podcast-editing-pipeline')))" "$PROJ")
PM=$(/usr/bin/python3 -c "import json,os,sys;print(os.path.expanduser(json.load(open(sys.argv[1])).get('personmask','~/.cache/podcast-editing-pipeline/bin/personmask')))" "$PROJ")
case "$VE$PM" in *:*) echo "venv/personmask paths must not contain ':'"; exit 1;; esac
command -v ffmpeg >/dev/null || { echo "install ffmpeg first (brew install ffmpeg)"; exit 1; }
if [ ! -x "$VE/bin/python" ]; then
  PY=/opt/homebrew/bin/python3; [ -x $PY ] || PY=python3
  $PY -m venv "$VE"
  "$VE/bin/pip" install -q mlx-whisper pillow numpy "opencv-python-headless<5"
fi
if [ ! -x "$PM" ]; then
  mkdir -p "$(dirname "$PM")"
  swiftc -O "$T/tools/personmask.swift" -o "$PM"
fi
echo "venv: $VE"; echo "personmask: $PM"
"$VE/bin/python" "$T/prep.py" --project "$PROJ"
