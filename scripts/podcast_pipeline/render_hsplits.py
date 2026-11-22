"""Pre-render every 16x9 split shot as one framed clip (side-by-side halves can't be built in FCP without
crops, and FCP renders crops black). Log cameras stay UNGRADED so the clip takes the same Camera LUT as the
camera files in FCP. Files: <outputs.fcp_media>/<reel>/h_split_<startframe>.mov (start frame = timeline frame).
Usage: render_hsplits.py [--project project.json] clips.json [name ...]   (run before restructure_fcpxml.py)"""
import json, os, sys
import render3 as R

PJ = R.PJ
fr = lambda t: int(round(t * PJ.fps))
args = sys.argv[1:]
clips = json.load(open(args[0] if args else f"{R.P}/clips.json")); want = set(args[1:])
MEDIA = PJ.outputs["fcp_media"]
for c in clips:
    if want and c["name"] not in want: continue
    sess, name, ranges = c["session"], c["name"], c["ranges"]
    R.set_look(name); R.set_clip(c)
    shots = R.shots_for(c)
    tl, total = R.timeline(ranges)
    def out_t(src):
        for a, b, o in tl:
            if a - 0.01 <= src <= b + 0.01: return o + src - a
    od = f"{MEDIA}/{name}"; os.makedirs(od, exist_ok=True); n = 0
    for s in shots:
        if s["kind"] != "split": continue
        o0, o1 = fr(out_t(s["a"])), fr(out_t(s["b"]))
        if o1 <= o0: continue
        ins, fc = R.shot_filter(s, sess, "h", 1920, 1080, graded=False)
        R.encode_shot(ins, fc, (o1 - o0) / PJ.fps + 0.05, f"{od}/h_split_{o0:06d}.mov"); n += 1
    print(name, n, "16x9 splits", flush=True)
