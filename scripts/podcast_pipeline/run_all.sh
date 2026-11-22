#!/bin/zsh
# Render every reel in clips.json (vertical + horizontal + quad proxy), 3 at a time.
# Usage: run_all.sh path/to/project.json [fmts=v,h,q]
# Test ONE reel first:  $VE/bin/python render3.py --project project.json clips.json --fmt v,h,q 01_name
set -e
T="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; D="$(dirname "$PROJ")"
VE=$(/usr/bin/python3 -c "import json,os,sys;print(os.path.expanduser(json.load(open(sys.argv[1])).get('venv','~/.venvs/podcast-editing-pipeline')))" "$PROJ")
N=$(/usr/bin/python3 -c "import json,sys;print(' '.join(c['name'] for c in json.load(open(sys.argv[1]))))" "$D/clips.json")
echo $N | tr ' ' '\n' | xargs -P 3 -I{} "$VE/bin/python" "$T/render3.py" --project "$PROJ" "$D/clips.json" --fmt "${2:-v,h,q}" {}
