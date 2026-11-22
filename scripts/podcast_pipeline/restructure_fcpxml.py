"""Rewrite export_fcpxml.py output into the structure Final Cut itself exports (FCP 13 rules).

Usage: restructure_fcpxml.py [--project project.json] in.fcpxml out.fcpxml [clips.json]

Final Cut dropped every connected clip that wasn't at 0:00 when they hung off one long gap.
Here each project becomes:
  spine        the lane-1 picture clips back to back (close-up / punch-in / wide / guest half of a split)
  lane 1/2     connected to the spine clip they start on: split listener half, opener title, mask copy
  lane -1      recorder mix, connected to the spine clip it starts on
Connected-clip offsets are expressed in the parent clip's source time, as FCP expects.
Also: NO crops (FCP renders them black) - default Fit conform with scale/position only; vertical split
listener at exactly half height on lane 1; 16x9 splits swapped for pre-rendered clips (render_hsplits.py);
title text gets balanced line breaks and the configured FCP font (Bebas renders garbled in FCP)."""
import sys, os, json, subprocess, urllib.parse, xml.etree.ElementTree as ET
from fractions import Fraction as F
import cfg

PJ = cfg.get()
SW, SH = PJ.src_w, PJ.src_h
FPS = PJ.fps

def t(s):
    s = s[:-1]
    return F(s) if "/" not in s else F(int(s.split("/")[0]), int(s.split("/")[1]))
def ts(x):
    x = F(x)
    if x == 0: return "0s"
    if x.denominator == 1: return f"{x.numerator}s"
    # keep FCP-style denominators where possible
    for d in (FPS.numerator, 24000, 30000, 60000, 2400, 2500, 3000, 25, 48000):
        if (x * d).denominator == 1: return f"{int(x * d)}/{d}s"
    return f"{x.numerator}/{x.denominator}s"

def wrap(text, limit):
    from itertools import combinations
    words = text.split()
    best = None
    for n in range(1, min(6, len(words)) + 1):
        # balanced split into n lines: brute force over break positions (few words)
        for br in combinations(range(1, len(words)), n - 1):
            idx = (0,) + br + (len(words),)
            lines = [" ".join(words[idx[i]:idx[i + 1]]) for i in range(n)]
            m = max(len(l) for l in lines)
            if best is None or (m <= limit and (best[0] > limit or n < best[1] or (n == best[1] and m < best[0]))) or (best[0] > limit and m < best[0]):
                best = (m, n, lines)
        if best and best[0] <= limit: break
    return "\n".join(best[2])

MEDIA = PJ.outputs["fcp_media"]
CLIPS = []
def reframe(root, gap, kids, vertical, pname):
    """No crops (FCP renders them black). Default Fit conform + scale/position only:
    full-frame shots keep the same framing; vertical split = guest on top (spine), listener below at
    exactly half-frame height (y -25% = -480 px) so nothing spills over the guest. 16x9 splits use a
    pre-rendered side-by-side clip."""
    W, H = (1080, 1920) if vertical else (1920, 1080)
    f = min(W / SW, H / SH)                    # the scale FCP's Fit conform already applies
    reel = next(c["name"] for c in CLIPS if pname.startswith(c["name"].split("_")[0] + " "))  # "01_slug" -> "01", "t12_slug" -> "t12"
    res = root.find("resources")
    for k in kids:
        if k.tag != "asset-clip" or k.find("adjust-crop") is None: continue
        xf = k.find("adjust-transform")
        sc = float(xf.get("scale").split()[0])
        k.remove(k.find("adjust-crop")); k.remove(k.find("adjust-conform"))
        split = k.get("name", "").startswith("Split")
        if split and not vertical:
            if k.get("lane") == "2": gap.remove(k); continue
            o = round(t(k.get("offset")) * FPS)
            mov = f"{MEDIA}/{reel}/h_split_{o:06d}.mov"
            if not os.path.exists(mov): raise SystemExit(f"missing {mov} - run render_hsplits.py first")
            n = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                                    "stream=nb_read_packets", "-of", "csv=p=0", mov], capture_output=True, text=True).stdout.strip())
            aid = f"hs_{reel[:2]}_{o}"
            a = ET.SubElement(res, "asset", id=aid, name=os.path.basename(mov), start="0s", duration=ts(F(n) / FPS),
                              hasVideo="1", format="fH")
            ET.SubElement(a, "media-rep", kind="original-media", src="file://" + urllib.parse.quote(mov))
            k.set("ref", aid); k.set("start", "0s"); k.set("name", "Split (pre-framed)"); k.remove(xf)
            continue
        if split and vertical and k.get("lane") == "2":   # listener, bottom half: exactly half the frame height
            s2 = (H / 2) / (SH * f); xf.set("scale", f"{s2:.5f} {s2:.5f}"); xf.set("position", "0 -25")
        else:
            xf.set("scale", f"{sc / f:.5f} {sc / f:.5f}")

def main(src, dst):
    tree = ET.parse(src); root = tree.getroot()
    formats = {f.get("id"): f for f in root.iter("format")}
    ignore_tc = {f"fC_{k}" for k, v in PJ.cams.items() if v.get("timecode", "ignore") != "tmcd"}
    log_fmt = {f"fC_{k}" for k, v in PJ.cams.items() if v.get("log")}
    # Sony XAVC files keep timecode only in their rtmd track, which FCP ignores when linking from XML:
    # it treats them as starting at 0. Re-base those assets (and every clip that uses them) to 0s.
    rebase = {}
    for a in root.iter("asset"):
        if a.get("format") in ignore_tc:
            rebase[a.get("id")] = t(a.get("start")); a.set("start", "0s")
    for c in root.iter("asset-clip"):
        if c.get("ref") in rebase:
            c.set("start", ts(t(c.get("start")) - rebase[c.get("ref")]))
    for proj in root.iter("project"):
        seq = proj.find("sequence"); spine = seq.find("spine"); gap = spine.find("gap")
        vertical = formats[seq.get("format")].get("height") == "1920"
        kids = list(gap)
        reframe(root, gap, kids, vertical, proj.get("name"))
        kids = list(gap)
        prim = sorted([k for k in kids if k.get("lane") == "1"], key=lambda k: t(k.get("offset")))
        others = [k for k in kids if k.get("lane") != "1"]
        tot = t(seq.get("duration"))
        new = ET.Element("spine"); placed = []  # (seq_start, seq_end, element, src_start)
        cur = F(0)
        for k in prim:
            o, d = t(k.get("offset")), t(k.get("duration"))
            if o > cur:  # hole -> gap
                g = ET.SubElement(new, "gap", name="Gap", offset=ts(cur), start="3600s", duration=ts(o - cur))
                placed.append((cur, o, g, F(3600)))
            k.attrib.pop("lane")
            k.set("offset", ts(o)); new.append(k); placed.append((o, o + d, k, t(k.get("start"))))
            cur = o + d
        if cur < tot:
            g = ET.SubElement(new, "gap", name="Gap", offset=ts(cur), start="3600s", duration=ts(tot - cur))
            placed.append((cur, tot, g, F(3600)))
        for k in others:
            o = t(k.get("offset")); lane = int(k.get("lane"))
            par = next(p for p in placed if p[0] <= o < p[1]) if o < tot else placed[-1]
            k.set("lane", str(lane - 1 if lane > 1 else lane))
            k.set("offset", ts(par[3] + (o - par[0])))
            if k.tag == "title":
                tx = k.find("text/text-style")
                tx.text = wrap(tx.text, 11 if vertical else 17)
                st = k.find("text-style-def/text-style")
                st.set("font", PJ.fonts["fcp_title_font"]); st.set("fontFace", PJ.fonts["fcp_title_face"])
            # connected items must come after the parent's intrinsic adjustments
            par[2].append(k)
        seq.remove(spine); seq.append(new)
    # Browser clips for every log source (camera files + pre-framed splits), tagged with a keyword so the
    # Camera LUT can be set on all of them at once: Keyword Collection > Cmd-A > Info > Camera LUT.
    ev = root.find("library/event")
    for i, a in enumerate([a for a in root.iter("asset") if a.get("format") in log_fmt or a.get("id", "").startswith("hs_")]):
        c = ET.Element("asset-clip", ref=a.get("id"), name=a.get("name"), start=a.get("start"), duration=a.get("duration"))
        ET.SubElement(c, "keyword", start=a.get("start"), duration=a.get("duration"), value=PJ.fcp["lut_keyword"])
        ev.insert(i, c)
    xml = ET.tostring(root, encoding="unicode")
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    open(dst, "w").write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + xml + "\n")
    print("wrote", dst)

if __name__ == "__main__":
    a = sys.argv[1:]
    CLIPS = json.load(open(a[2] if len(a) > 2 else f"{PJ.dir}/clips.json"))
    main(a[0], a[1])
