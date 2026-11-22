"""Flag frames where a person's head touches the top of the frame (or the top of the lower split panel).
Usage: audit_headroom.py [--project project.json] <out.json> video.mp4 [...]   (samples 2 fps)"""
import sys, os, json, subprocess, glob, shutil, numpy as np
from PIL import Image
import cfg

PJ = cfg.get()
PM = PJ.personmask
TMP = os.path.join(PJ.cache, "audit")
TITLE = PJ.style["title_dur"]
out = {}
for v in sys.argv[2:]:
    name = ("h/" if os.path.dirname(os.path.abspath(v)) == os.path.abspath(PJ.outputs["horizontal"]) else "v/") + os.path.basename(v)[:-4]
    shutil.rmtree(TMP, ignore_errors=True); os.makedirs(TMP + "/f")
    subprocess.run(["ffmpeg", "-v", "error", "-i", v, "-vf", "fps=2,scale=iw/4:ih/4", TMP + "/f/%05d.png"], check=True)
    subprocess.run([PM, TMP + "/f", TMP + "/m"], check=True, capture_output=True)
    bad = []
    for p in sorted(glob.glob(TMP + "/m/*.png")):
        m = np.array(Image.open(p).convert("L")) > 128; H, W = m.shape
        t = (int(os.path.basename(p)[:-4]) - 1) / 2
        if t < TITLE: continue  # opener wide (depth title)
        edges = [0]
        # a split shows as a hard black seam; check the top of the lower panel too
        f = np.array(Image.open(p.replace("/m/", "/f/")).convert("L"))
        if name.startswith("v/") and f[H // 2 - 1:H // 2 + 1].mean() < 25: edges.append(H // 2 + 1)
        for e in edges:
            band = m[e:e + 3]
            # a head touching the edge: a solid run of person pixels along the edge, not just an arm
            if band.mean() > 0.04 and band.sum(0).max() >= 3: bad.append(round(t, 1)); break
    out[name] = bad
    print(f"{name}: {len(bad)} flagged frames {bad[:12]}", flush=True)
json.dump(out, open(sys.argv[1], "w"), indent=1)
