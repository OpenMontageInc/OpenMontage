"""Export every reel as Final Cut Pro timelines (FCPXML 1.11, imports into FCP 10.6+ incl. FCP 13).
Usage: export_fcpxml.py [--project project.json] clips.json [name ...]
Writes work/fcpxml_gap_version.fcpxml (intermediate - run restructure_fcpxml.py on it next)
and <outputs.fcpxml>/captions/<reel>.srt.
Timelines reference the ORIGINAL camera files + recorder mix (not the renders), using the same edit
plan and framing as render3.py. Intermediate layout per project, on one full-length gap:
  lane 1  picture (close-up / punch-in / wide), or the guest half of a split
  lane 2  the listener half of a split; on the opener, the title
  lane 3  opener only: a copy of the wide to put a Magnetic Mask on (title-behind-people)
  lane -1 recorder stereo mix (camera audio is not used)
FCP 13 drops connected clips hung off one long gap - restructure_fcpxml.py turns this into a spine."""
import json, os, sys, subprocess, urllib.parse, html
from fractions import Fraction
import cfg
import render3 as R

PJ = R.PJ
SW, SH = PJ.src_w, PJ.src_h
FPS = PJ.fps                                 # sequence rate, e.g. 24000/1001
TB, FD = FPS.numerator, FPS.denominator      # sequence frame = FD/TB s
def fr(t): return int(round(t * TB / FD))    # seconds -> sequence frames
def T(frames): return "0s" if frames == 0 else f"{frames * FD}/{TB}s"
def TS(sec, rate):                           # source time on that source's own frame grid
    rate = Fraction(rate); n = int(round(sec * rate))
    return "0s" if n == 0 else f"{n * rate.denominator}/{rate.numerator}s"
def tc_sec(tc, rate):
    h, m, s, f = [int(x) for x in tc.replace(";", ":").split(":")]
    base = round(float(rate))
    return (((h * 60 + m) * 60 + s) * base + f) / Fraction(rate)
def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=timecode:stream_tags=timecode",
                        "-of", "json", path], capture_output=True, text=True)
    j = json.loads(r.stdout); tc = j["format"].get("tags", {}).get("timecode")
    for st in j.get("streams", []):
        tc = tc or st.get("tags", {}).get("timecode")
    return float(j["format"]["duration"]), tc
def frame_dur(rate):                        # FCP style: 1001/24000s, 100/2500s
    rate = Fraction(rate)
    return f"100/{rate.numerator * 100}s" if rate.denominator == 1 else f"{rate.denominator}/{rate.numerator}s"
def url(p): return "file://" + urllib.parse.quote(p)
def esc(s): return html.escape(s, quote=True)

# ---------- cameras -> FCP formats ----------
RATE_CODE = {Fraction(24000, 1001): "2398", Fraction(24): "24", Fraction(25): "25", Fraction(30000, 1001): "2997",
             Fraction(30): "30", Fraction(50): "50", Fraction(60000, 1001): "5994", Fraction(60): "60"}
def cam_rate(cam): return cfg.rate_frac(PJ.cams[cam].get("rate", PJ.fps_str))
def fmt_id(cam): return f"fC_{cam}"
_path_cam = {}
def cam_for_path(path):
    if not _path_cam:
        for sid in PJ.sessions:
            _path_cam[PJ.ref_file(sid)] = PJ.ref
            for cam, ms in PJ.sessions[sid].get("media", {}).items():
                for m in ms: _path_cam[PJ.res(m["file"])] = cam
    return _path_cam[path]

# ---------- assets ----------
assets = {}   # path -> dict(id, start_sec, rate, kind)
def asset(path, kind):
    if path in assets: return assets[path]
    dur, tc = probe(path)
    if kind == "audio":
        assets[path] = dict(id=f"a{len(assets) + 1}", start=0.0, dur=dur, rate=48000, kind=kind, name=os.path.basename(path))
        return assets[path]
    cam = cam_for_path(path); rate = cam_rate(cam)
    # FCP only honours a real tmcd track. Sony XAVC keeps timecode in rtmd, which FCP ignores when linking
    # from XML (it treats the file as starting at 0) -> "timecode": "ignore" puts those assets at 0s.
    start = tc_sec(tc, rate) if (tc and PJ.cams[cam].get("timecode", "ignore") == "tmcd") else 0.0
    assets[path] = dict(id=f"a{len(assets) + 1}", start=start, dur=dur, rate=rate, kind=kind, cam=cam, name=os.path.basename(path))
    return assets[path]

# ---------- framing (same math as render3.shot_filter) ----------
def crop_for(s, part, sess, fmt, W, H):
    """Return (path, source_sec, x, y, cw, ch, panel(cx,cy,pw,ph)) for one picture element of a shot."""
    dur = s["b"] - s["a"]
    if s["kind"] == "wide":
        f, off = R.media_for(sess, PJ.wide, s["a"])
        x, y, cw, ch = R.wide_crop(fmt)
        return f, s["a"] - off, x, y, cw, ch, (0, 0, W, H)
    if s["kind"] == "cu":
        cw, ch, hf = R.cu_size(s["who"], fmt)
        f, ss, x, y = R.person_crop(sess, s["who"], s["a"], dur, cw, ch, hf)
        if s.get("punch"): x, y, cw, ch = x + 0.065 * cw, y, 0.87 * cw, 0.87 * ch
        return f, ss, x, y, cw, ch, (0, 0, W, H)
    cw, ch, hf = R.split_size(fmt)
    if fmt == "v":
        pw, ph = W, H // 2
        who, pc = (("guest", (0, H / 4)), (s["low"], (0, -H / 4)))[part]
    else:
        pw, ph = W // 2, H
        who, pc = ((s["low"], (-W / 4, 0)), ("guest", (W / 4, 0)))[part]
    f, ss, x, y = R.person_crop(sess, who, s["a"], dur, cw, ch, hf)
    return f, ss, x, y, cw, ch, (pc[0], pc[1], pw, ph)

def video_clip(path, src_sec, x, y, cw, ch, panel, lane, off_f, dur_f, H, name):
    a = asset(path, "video")
    pcx, pcy, pw, ph = panel
    s = pw / cw
    tx = pcx - (x + cw / 2 - SW / 2) * s        # FCP: origin at frame centre, y up
    ty = pcy + (y + ch / 2 - SH / 2) * s
    trim = (x / SW * 100, y / SH * 100, (SW - x - cw) / SW * 100, (SH - y - ch) / SH * 100)
    return (f'<asset-clip ref="{a["id"]}" lane="{lane}" offset="{T(off_f)}" name="{esc(name)}" '
            f'start="{TS(a["start"] + src_sec, a["rate"])}" duration="{T(dur_f)}" srcEnable="video">'
            f'<adjust-crop mode="trim"><trim-rect left="{trim[0]:.4f}" top="{trim[1]:.4f}" right="{trim[2]:.4f}" bottom="{trim[3]:.4f}"/></adjust-crop>'
            f'<adjust-conform type="none"/>'
            f'<adjust-transform position="{tx / H * 100:.4f} {ty / H * 100:.4f}" scale="{s:.5f} {s:.5f}"/>'
            f'</asset-clip>')

LABEL = {k: PJ.people[k]["label"] for k in ("guest", "host", "cohost")}; LABEL["wide"] = "Wide"
REC = PJ.recorder.get("label", "Recorder")
def project(clip, fmt, shots, title_ref):
    sess, name, ranges = clip["session"], clip["name"], clip["ranges"]
    W, H = R.FMT[fmt]["W"], R.FMT[fmt]["H"]
    tl, total = R.timeline(ranges)
    def out_t(src):
        for a, b, o in tl:
            if a - 0.01 <= src <= b + 0.01: return o + src - a
    items = []
    for s in shots:
        o0, o1 = fr(out_t(s["a"])), fr(out_t(s["b"]))
        if o1 <= o0: continue
        parts = (0, 1) if s["kind"] == "split" else (0,)
        for k in parts:
            f, ss, x, y, cw, ch, panel = crop_for(s, k, sess, fmt, W, H)
            if s["kind"] == "split":
                who = ("guest", s["low"])[k] if fmt == "v" else (s["low"], "guest")[k]
                nm = f"Split · {LABEL[who]}"
            else:
                nm = LABEL[s["who"]] + (" · punch-in" if s.get("punch") else "")
            items.append(video_clip(f, ss, x, y, cw, ch, panel, k + 1, o0, o1 - o0, H, nm))
        if s.get("title"):
            f, ss, x, y, cw, ch, panel = crop_for(s, 0, sess, fmt, W, H)
            size = 215 if fmt == "v" else 250
            items.append(f'<title ref="{title_ref}" lane="2" offset="{T(o0)}" name="Title" start="3600s" duration="{T(o1 - o0)}">'
                         f'<text><text-style ref="ts_{name}_{fmt}">{esc(clip["title"].upper())}</text-style></text>'
                         f'<text-style-def id="ts_{name}_{fmt}"><text-style font="{esc(PJ.fonts["fcp_title_font"])}" fontSize="{size}" fontColor="0.086 0.102 0.118 1" alignment="center"/></text-style-def></title>')
            items.append(video_clip(f, ss, x, y, cw, ch, panel, 3, o0, o1 - o0, H, "Wide · add Magnetic Mask (person) for title-behind-people"))
    # audio: recorder mix per range, frame-accurate joins
    mix = PJ.recorder.get("mix", "Stereo Mix.wav")
    for a, b, o in tl:
        d, envn, _, off = R.rc_for(sess, a); off = R.refine(PJ.env_ref(sess), envn, off, a + 1)
        ast = asset(f"{d}/{mix}", "audio")
        o0, o1 = fr(o), fr(o + b - a)
        items.append(f'<asset-clip ref="{ast["id"]}" lane="-1" offset="{T(o0)}" name="{esc(REC)} mix" '
                     f'start="{TS(ast["start"] + a - off, 48000)}" duration="{T(o1 - o0)}" audioRole="dialogue"/>')
    tot = fr(total)
    pname = f'{name[:2]} {clip["title"]} ({"9x16" if fmt == "v" else "16x9"})'.replace('"', "")
    return (f'<project name="{esc(pname)}"><sequence format="{"fV" if fmt == "v" else "fH"}" duration="{T(tot)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">'
            f'<spine><gap name="Gap" offset="0s" start="0s" duration="{T(tot)}">' + "".join(items) + '</gap></spine></sequence></project>')

def srt(clip, path):
    sess, ranges = clip["session"], clip["ranges"]; tl, _ = R.timeline(ranges)
    def out_t(src):
        for a, b, o in tl:
            if a - 0.05 <= src <= b + 0.05: return o + min(max(src, a), b) - a
    words = [w for a, b in ranges for w in R.words_in(sess, a, b)]
    def ts(t): ms = int(round(t * 1000)); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    with open(path, "w") as fh:
        for i, (txt, a, b) in enumerate(R.chunk_words(words), 1):
            oa, ob = out_t(a), out_t(b)
            if oa is None or ob is None: continue
            fh.write(f"{i}\n{ts(oa)} --> {ts(max(ob, oa + 0.3))}\n{txt}\n\n")

def seq_format(fid, W, H):
    code = RATE_CODE.get(FPS)
    name = f"FFVideoFormat{H}p{code}" if (W, H) == (1920, 1080) and code else "FFVideoFormatRateUndefined"
    return f'<format id="{fid}" name="{name}" frameDuration="{frame_dur(FPS)}" width="{W}" height="{H}" colorSpace="1-1-1 (Rec. 709)"/>'

if __name__ == "__main__":
    args = sys.argv[1:]
    clips = json.load(open(args[0] if args else f"{R.P}/clips.json")); want = set(args[1:])
    od = PJ.outputs["fcpxml"]; os.makedirs(od + "/captions", exist_ok=True)
    projects = []
    for c in clips:
        if want and c["name"] not in want: continue
        R.set_look(c["name"]); R.set_clip(c)
        shots = R.shots_for(c)
        for fmt in ("v", "h"): projects.append(project(c, fmt, shots, "rTitle"))
        srt(c, f"{od}/captions/{c['name']}.srt")
        print(c["name"], len(shots), "shots", flush=True)
    res = [seq_format("fV", 1080, 1920), seq_format("fH", 1920, 1080)]
    for cam in PJ.cams:
        r = cam_rate(cam); code = RATE_CODE.get(r, "")
        res.append(f'<format id="{fmt_id(cam)}" name="FFVideoFormat{SH}p{code}" frameDuration="{frame_dur(r)}" '
                   f'width="{SW}" height="{SH}" colorSpace="1-1-1 (Rec. 709)"/>')
    res.append('<effect id="rTitle" name="Basic Title" uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"/>')
    for p, a in assets.items():
        if a["kind"] == "audio":
            res.append(f'<asset id="{a["id"]}" name="{esc(a["name"])}" start="0s" duration="{TS(a["dur"], 48000)}" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">'
                       f'<media-rep kind="original-media" src="{url(p)}"/></asset>')
        else:
            res.append(f'<asset id="{a["id"]}" name="{esc(a["name"])}" start="{TS(a["start"], a["rate"])}" duration="{TS(a["dur"], a["rate"])}" '
                       f'hasVideo="1" format="{fmt_id(a["cam"])}" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">'
                       f'<media-rep kind="original-media" src="{url(p)}"/></asset>')
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.11"><resources>' + "".join(res) +
           f'</resources><library><event name="{esc(PJ.fcp["event"])}">' + "".join(projects) + '</event></library></fcpxml>\n')
    os.makedirs(PJ.work, exist_ok=True)
    out = f"{PJ.work}/fcpxml_gap_version.fcpxml"; open(out, "w").write(xml)
    print("wrote", out, len(xml), "bytes;", len(projects), "projects;", len(assets), "assets")
    print("next: restructure_fcpxml.py", repr(out), repr(f"{od}/{PJ.fcp.get('fcpxml_name', PJ.fcp['event'] + '.fcpxml')}"))
