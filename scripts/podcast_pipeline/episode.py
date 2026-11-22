"""Full-length multicam episodes (16x9 1920x1080) from the same show setup the reels use.

Usage: episode.py [--project project.json] [episodes.json] plan
       episode.py [--project project.json] [episodes.json] render [name ...] [--test SECONDS] [--workers N]
       episode.py [--project project.json] [episodes.json] fcpxml [name ...]

episodes.json (in the show folder by default) lists one entry per episode:
  {"name": "01_slug", "session": 1, "title": "Show - Guest",
   "keep": [[in, out], ...],      # reference-clock seconds; leave out pre-show / breaks / post-show
   "max_gap": 2.5,                # silences longer than this are squeezed to ~1 s ("light trim")
   "demo": [[a, b], ...]}         # optional: people off their marks (a product demo) -> wide / guest cam only

plan    auto multicam edit -> <show>/shots/ep_<name>.json (re-used by render + fcpxml; delete to re-plan)
render  <episodes_root>/<name>.mp4: graded, H.264 CRF 16, recorder mix at -16 LUFS (two-pass), sample-exact
        to the frame grid. --test N renders only the first N seconds (_test_<name>.mp4).
fcpxml  Final Cut Pro 13 timeline of every episode, on the ORIGINAL camera files + recorder mix:
        export (gap version) -> pre-framed 16x9 split clips -> restructure_fcpxml.py (FCP 13 rules), plus SRTs.

Same signals as the reels (render3.py): cut word by word on who is speaking (recorder mic levels), reaction
splits when the host/co-host interjects. Long-form rhythm on top: close-up / punch-in / two-shot / wide every
5-12 s through long answers, a 6 s wide opener. Over an hour the cameras drift, so single-file cameras are
re-synced every 120 s, and the recorder every 300 s; framing is measured once per person per 90 s (locked-off cameras)."""
import json, os, sys, subprocess, shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import cfg
import render3 as R
import export_fcpxml as X

PJ = R.PJ
ARGS = sys.argv[1:]
EP_FILE = ARGS.pop(0) if ARGS and ARGS[0].endswith(".json") else os.path.join(PJ.dir, "episodes.json")
EPISODES = json.load(open(EP_FILE))
EP_ROOT = PJ.res(PJ.c.get("episodes_root", "EPISODES"))
FPS = PJ.fps
fr = X.fr

# ---------------- ranges (light trim) ----------------
def all_words(sess):
    R.words_in(sess, 0, 0)
    return [w for seg in R._tr[sess]["segments"] for w in seg.get("words", [])]

def ep_ranges(ep):
    """Episode in/out + cuts, then any silence over max_gap squeezed to ~1 s."""
    W = all_words(ep["session"]); out = []
    for a, b in ep["keep"]:
        ws = [w for w in W if w["start"] >= a and w["end"] <= b]
        cur = a
        for p, q in zip(ws, ws[1:]):
            if q["start"] - p["end"] > ep.get("max_gap", 2.5):
                out.append([cur, p["end"] + 0.5]); cur = q["start"] - 0.5
        out.append([cur, b])
    return [[round(a, 3), round(b, 3)] for a, b in out]

def out_map(ranges):
    tl, total = R.timeline(ranges)
    def out_t(src):
        for a, b, o in tl:
            if a - 0.01 <= src <= b + 0.01: return o + min(max(src, a), b) - a
    return tl, total, out_t

# ---------------- clock drift: local re-sync ----------------
_sync = {}
def resync(sess, t):
    """Point render3's single-file camera offsets at the 120 s bucket around ref-time t."""
    R._clip_off.clear()
    for cam in PJ.cams:
        if cam in (PJ.ref, PJ.wide): continue
        files = PJ.media(sess, cam)
        if len(files) != 1: continue
        key = PJ.env_media(files[0][0])
        if not os.path.exists(PJ.env_file(key)): continue
        b = int(t // 120); k = (sess, cam, b)
        if k not in _sync:
            _sync[k] = R.refine(PJ.env_ref(sess), key, files[0][1], max(b * 120 + 60, 1.0))
        R._clip_off[(sess, cam)] = _sync[k]

def wide_at(sess, t):
    """(file, offset, duration) of the wide-camera file covering t (last file that started by t)."""
    best = None
    for f, off, dur in PJ.media(sess, PJ.wide):
        if off <= t + 0.05: best = (f, off, dur or 1e9)
    if best is None: raise ValueError(f"no wide footage at {t}")
    return best

# ---------------- shot planning ----------------
def in_demo(ep, t): return any(a <= t < b for a, b in ep.get("demo", []))

class Rhythm:
    def __init__(self): self.n = 0
    def nxt(self): self.n += 1; return self.n

def run(a, b, pattern, rh, tail_who):
    """Fill a..b with a repeating pattern of (kind, who, secs, punch); the remainder is a close-up of tail_who."""
    out, t, i = [], a, rh.nxt()
    while True:
        kind, who, d, punch = pattern[i % len(pattern)]
        if b - t <= d + 4.0: break
        out.append(dict(a=t, b=t + d, kind=kind, who=who, punch=punch)); t += d; i += 1
    out.append(dict(a=t, b=b, kind="cu", who=tail_who, punch=(i % 2 == 1)))
    return out

GUEST_PAT = [("cu", "guest", 12, False), ("cu", "guest", 10, True), ("two", "two", 4.5, False),
             ("cu", "guest", 12, False), ("wide", "wide", 5.5, False)]
def host_pat(who): return [("cu", who, 10, False), ("cu", who, 9, True), ("cu", "guest", 4, False),
                           ("cu", who, 10, False), ("wide", "wide", 5, False)]

def angle_key(s): return (s["kind"], s["who"], s.get("low"), bool(s.get("punch")))

def plan_long(ep, ranges):
    sess = ep["session"]; rh = Rhythm(); shots = []; turns, reacts = [], []
    for a, b in ranges:
        t, r = R.speaker_turns(sess, a, b, min_turn=1.2, lead=0.15); turns += t; reacts += r
    for a, b, who in turns:
        if who != "guest":
            shots += run(a, b, host_pat(who), rh, who) if b - a > 15 else [dict(a=a, b=b, kind="cu", who=who, punch=False)]
            continue
        rs = sorted([(max(a, r0 - 0.4), min(b, max(r1 + 1.2, r0 + 2.5)), rw) for r0, r1, rw in reacts
                     if a <= r0 < b and rw in ("host", "cohost")])
        merged = []
        for s0, s1, rw in rs:
            if merged and s0 < merged[-1][1] + 1.5: merged[-1][1] = max(merged[-1][1], s1)
            else: merged.append([s0, s1, rw])
        t = a
        for s0, s1, rw in merged:
            if s0 - t < 2.0: s0 = t
            if b - s1 < 2.0: s1 = b
            if s0 > t: shots += run(t, s0, GUEST_PAT, rh, "guest")
            shots.append(dict(a=s0, b=s1, kind="split", who="guest", low=rw)); t = s1
        if t < b: shots += run(t, b, GUEST_PAT, rh, "guest")
    k = 0   # demo stretch: people off their marks -> alternate wide / guest camera
    for s in shots:
        if in_demo(ep, s["a"]) and s["kind"] != "wide":
            k += 1
            if k % 2: s.update(kind="wide", who="wide", punch=False)
            else: s.update(kind="cu", who="guest", punch=False)
            s.pop("low", None)
    a0 = ranges[0][0]; op = dict(a=a0, b=a0 + 6.0, kind="wide", who="wide", punch=False)
    out = [op]
    for s in shots:
        if s["b"] <= op["b"] + 0.05: continue
        if s["a"] < op["b"]: s = dict(s, a=op["b"])
        out.append(s)
    res = []
    for s in out:   # merge slivers, then same-angle neighbours
        joined = res and abs(res[-1]["b"] - s["a"]) < 0.01
        if joined and (s["b"] - s["a"] < 1.6 or angle_key(res[-1]) == angle_key(s)): res[-1]["b"] = s["b"]; continue
        res.append(s)
    out = []
    for s in res:   # a wide shot never runs off the end of a wide-camera file
        while s["kind"] == "wide":
            f, off, dur = wide_at(sess, s["a"]); end = off + dur - 0.05
            if s["b"] <= end: break
            out.append(dict(s, b=end)); s = dict(s, a=end)
        out.append(s)
    res = []
    for s in out:   # a sliver left by a file change goes to the shot before it
        if res and s["b"] - s["a"] < 1.6 and res[-1]["kind"] != "wide" and abs(res[-1]["b"] - s["a"]) < 0.01:
            res[-1]["b"] = s["b"]; continue
        res.append(s)
    for i, s in enumerate(res[:-1]):   # a sliver right after a cut (range join) borrows from the next shot
        nx = res[i + 1]; need = 1.6 - (s["b"] - s["a"])
        if need > 0 and abs(nx["a"] - s["b"]) < 0.01 and nx["b"] - nx["a"] > 1.6 + need:
            s["b"] += need; nx["a"] = s["b"]
    return res

# ---------------- framing (bucketed) ----------------
_orig_person_crop = R.person_crop
_pcc = {}
CUR = {}
def person_crop(sess, who, a, dur, cw, ch, head_frac):
    """render3.person_crop measured once per person per 90 s (cameras are locked off); the guest camera and
    demo stretches (people move) are measured on the shot itself."""
    ep, ranges = CUR["ep"], CUR["ranges"]; b = int(a // 90)
    k = (sess, who, cw, ch, head_frac, b)
    if k not in _pcc:
        mid = min(max(b * 90 + 45, ranges[0][0] + 1), ranges[-1][1] - 9)
        if PJ.people[who]["camera"] != PJ.ref or in_demo(ep, mid) != in_demo(ep, a): mid = a
        resync(sess, mid)
        _, _, x, y = _orig_person_crop(sess, who, mid, 8.0, cw, ch, head_frac)
        _pcc[k] = (x, y)
    resync(sess, a)
    f, ss = R.source(sess, who, a)
    return (f, ss) + _pcc[k]
R.person_crop = person_crop

def crop_for(s, part, sess, W=1920, H=1080):
    resync(sess, s["a"])
    if s["kind"] == "two": return PJ.ref_file(sess), s["a"], 0, 0, PJ.src_w, PJ.src_h, (0, 0, W, H)
    if s["kind"] == "wide":
        f, off, _ = wide_at(sess, s["a"]); return f, s["a"] - off, 0, 0, PJ.src_w, PJ.src_h, (0, 0, W, H)
    return X.crop_for(s, part, sess, "h", W, H)

def load(ep):
    ranges = ep_ranges(ep); CUR.update(ep=ep, ranges=ranges); R.set_look(ep["name"])
    f = f"{PJ.dir}/shots/ep_{ep['name']}.json"
    if os.path.exists(f):
        d = json.load(open(f))
        if d["ranges"] == ranges: return ranges, d["shots"]
    shots = plan_long(ep, ranges)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    json.dump(dict(ranges=ranges, shots=shots), open(f, "w"), indent=0)
    return ranges, shots

# ---------------- render ----------------
def vfilter(s, sess, W=1920, H=1080):
    """(inputs, filter_complex -> [v]); scales before grading (a LUT on 4K frames is the slow part)."""
    g = lambda cam: R.grade(cam) + R.LOOK
    if s["kind"] in ("wide", "two"):
        f, ss, *_ = crop_for(s, 0, sess, W, H); cam = PJ.wide if s["kind"] == "wide" else PJ.ref
        return [(f, ss)], f"[0:v]scale={W}:{H}:flags=lanczos,{g(cam)},fps={PJ.fps_str},setsar=1[v]"
    if s["kind"] == "cu":
        f, ss, x, y, cw, ch, _ = crop_for(s, 0, sess, W, H)
        return [(f, ss)], (f"[0:v]crop={int(cw)}:{int(ch)}:{int(x)}:{int(y)},scale={W}:{H}:flags=lanczos,"
                           f"{g(R.cam_of(s['who']))},fps={PJ.fps_str},setsar=1[v]")
    ins, parts = [], []
    for k, who in enumerate((s["low"], "guest")):
        f, ss, x, y, cw, ch, _ = crop_for(s, k, sess, W, H); ins.append((f, ss))
        parts.append(f"[{k}:v]crop={int(cw)}:{int(ch)}:{int(x)}:{int(y)},scale={W // 2}:{H}:flags=lanczos,"
                     f"{g(R.cam_of(who))},fps={PJ.fps_str},setsar=1[p{k}]")
    return ins, ";".join(parts) + ";[p0][p1]hstack=inputs=2,drawbox=x=iw/2-2:y=0:w=4:h=ih:color=black@0.9:t=fill[v]"

def enc(job):
    ins, fc, nfr, out = job
    if os.path.exists(out) and os.path.getsize(out) > 0: return
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for f, ss in ins: cmd += ["-ss", f"{max(ss, 0):.3f}", "-t", f"{float(nfr / FPS) + 0.5:.3f}", "-i", f]
    cmd += ["-filter_complex", fc, "-map", "[v]", "-frames:v", str(nfr), "-an", "-r", PJ.fps_str,
            "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-video_track_timescale", str(FPS.numerator), out + ".tmp.mp4"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: raise RuntimeError(f"{out}: {r.stderr[-1500:]}")
    os.replace(out + ".tmp.mp4", out)

def chunks(a, b, step):
    return sorted(set([a + k * step for k in range(int((b - a) // step) + 1)] + [b]))

def build_audio(ep, ranges, tmp):
    """Recorder mix, sample-exact to the frame grid, re-synced every <=300 s; two-pass loudnorm."""
    sess = ep["session"]; tl, _, _ = out_map(ranges); parts = []
    mix = PJ.recorder.get("mix", "Stereo Mix.wav")
    smp = lambda g: int(round(g * 48000 / FPS))
    for ri, (a, b, o) in enumerate(tl):
        cs = chunks(a, b, 300)
        for ci, (c0, c1) in enumerate(zip(cs, cs[1:])):
            g0, g1 = fr(o + c0 - a), fr(o + c1 - a)
            if g1 <= g0: continue
            d, envn, _, off = R.rc_for(sess, c0); off = R.refine(PJ.env_ref(sess), envn, off, c0 + 1)
            n = smp(g1) - smp(g0); p = f"{tmp}/a_{ri:03d}_{ci:02d}.wav"
            af = []
            if ci == 0: af.append("afade=in:d=0.015")
            if ci == len(cs) - 2: af.append(f"afade=out:st={n / 48000 - 0.02:.4f}:d=0.02")
            R.sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{c0 - off:.5f}", "-i", f"{d}/{mix}", "-af", ",".join(af + ["apad"]),
                  "-ar", "48000", "-ac", "2", "-t", f"{n / 48000:.6f}", "-c:a", "pcm_s24le", p])
            parts.append(p)
    with open(f"{tmp}/a.txt", "w") as fh:
        for p in parts: fh.write(f"file '{p}'\n")
    raw = f"{tmp}/audio_raw.wav"
    R.sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{tmp}/a.txt", "-c", "copy", raw])
    pre = PJ.c.get("episode_audio_filter", "highpass=f=70,acompressor=threshold=-20dB:ratio=2.5:attack=10:release=200")
    I = PJ.c.get("episode_loudness", -16)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", raw, "-af", f"{pre},loudnorm=I={I}:TP=-1.5:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    ln = (f"loudnorm=I={I}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
          f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    out = f"{tmp}/audio.wav"
    R.sh(["ffmpeg", "-v", "error", "-y", "-i", raw, "-af", f"{pre},{ln}", "-ar", "48000", "-c:a", "pcm_s24le", out])
    return out

def render(ep, test=None, workers=4):
    ranges, shots = load(ep); sess = ep["session"]
    if test:
        tl, _, out_t = out_map(ranges)
        ranges = [[a, min(b, a + test - o)] for a, b, o in tl if o < test]
        shots = [dict(s, b=min(s["b"], ranges[-1][1])) for s in shots if out_t(s["a"]) < test]
    tl, total, out_t = out_map(ranges)
    tag = ("_test_" if test else "") + ep["name"]
    tmp = f"{PJ.cache}/episodes/{tag}"; os.makedirs(tmp, exist_ok=True)
    jobs = []
    for i, s in enumerate(shots):   # framing + sync on this thread (shared state), encodes in parallel
        o0, o1 = fr(out_t(s["a"])), fr(out_t(s["b"]))
        if o1 > o0: jobs.append(vfilter(s, sess) + (o1 - o0, f"{tmp}/v{i:04d}.mp4"))
    nfr = sum(j[2] for j in jobs)
    print(f"{tag}: {len(jobs)} shots, {float(nfr / FPS) / 60:.1f} min", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        for n, _ in enumerate(ex.map(enc, jobs), 1):
            if n % 25 == 0: print(f"  {n}/{len(jobs)} shots", flush=True)
    with open(f"{tmp}/v.txt", "w") as fh:
        for j in jobs: fh.write(f"file '{j[3]}'\n")
    R.sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{tmp}/v.txt", "-c", "copy", f"{tmp}/video.mp4"])
    audio = build_audio(ep, ranges, tmp)
    os.makedirs(EP_ROOT, exist_ok=True); out = f"{EP_ROOT}/{tag}.mp4"
    R.sh(["ffmpeg", "-v", "error", "-y", "-i", f"{tmp}/video.mp4", "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
          "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", "-shortest", out])
    print("wrote", out, flush=True)
    if not test: shutil.rmtree(tmp, ignore_errors=True)

# ---------------- Final Cut Pro ----------------
LABEL = dict(X.LABEL, two="Two-shot")
def proj_name(ep): return f'{ep["name"].split("_")[0]} {ep["title"]} (16x9 episode)'.replace('"', "")

def fcp_project(ep, ranges, shots):
    """Gap-version project (export_fcpxml layout) for restructure_fcpxml.py."""
    sess = ep["session"]; tl, total, out_t = out_map(ranges); items = []
    for s in shots:
        o0, o1 = fr(out_t(s["a"])), fr(out_t(s["b"]))
        if o1 <= o0: continue
        for k in ((0, 1) if s["kind"] == "split" else (0,)):
            f, ss, x, y, cw, ch, panel = crop_for(s, k, sess)
            nm = f"Split · {LABEL[(s['low'], 'guest')[k]]}" if s["kind"] == "split" else \
                 LABEL[s["who"]] + (" · punch-in" if s.get("punch") else "")
            items.append(X.video_clip(f, ss, x, y, cw, ch, panel, k + 1, o0, o1 - o0, 1080, nm))
    mix = PJ.recorder.get("mix", "Stereo Mix.wav")
    for a, b, o in tl:
        cs = chunks(a, b, 300)
        for c0, c1 in zip(cs, cs[1:]):
            g0, g1 = fr(o + c0 - a), fr(o + c1 - a)
            if g1 <= g0: continue
            d, envn, _, off = R.rc_for(sess, c0); off = R.refine(PJ.env_ref(sess), envn, off, c0 + 1)
            ast = X.asset(f"{d}/{mix}", "audio")
            items.append(f'<asset-clip ref="{ast["id"]}" lane="-1" offset="{X.T(g0)}" name="{X.esc(X.REC)} mix" '
                         f'start="{X.TS(ast["start"] + c0 - off, 48000)}" duration="{X.T(g1 - g0)}" audioRole="dialogue"/>')
    tot = fr(total)
    return (f'<project name="{X.esc(proj_name(ep))}"><sequence format="fH" duration="{X.T(tot)}" tcStart="0s" tcFormat="NDF" '
            f'audioLayout="stereo" audioRate="48k"><spine><gap name="Gap" offset="0s" start="0s" duration="{X.T(tot)}">'
            + "".join(items) + '</gap></spine></sequence></project>')

def hsplits(ep, ranges, shots, media):
    """16x9 splits pre-framed as single clips, ungraded log (they take the Camera LUT in FCP like the camera files)."""
    _, _, out_t = out_map(ranges); od = f"{media}/{ep['name']}"; os.makedirs(od, exist_ok=True)
    jobs = []
    for s in shots:
        if s["kind"] != "split": continue
        o0, o1 = fr(out_t(s["a"])), fr(out_t(s["b"]))
        out = f"{od}/h_split_{o0:06d}.mov"
        if o1 <= o0 or os.path.exists(out): continue
        resync(ep["session"], s["a"])
        ins, fc = R.shot_filter(s, ep["session"], "h", 1920, 1080, graded=False)
        jobs.append((ins, fc, float((o1 - o0) / FPS) + 0.05, out))
    with ThreadPoolExecutor(3) as ex: list(ex.map(lambda j: R.encode_shot(*j), jobs))
    print(ep["name"], len(jobs), "16x9 splits", flush=True)

def write_fcpxml(eps):
    od = f"{EP_ROOT}/FCPXML"; media = f"{EP_ROOT}/FCP MEDIA"; os.makedirs(od + "/captions", exist_ok=True)
    projects = []
    for ep in eps:
        ranges, shots = load(ep)
        projects.append(fcp_project(ep, ranges, shots))
        hsplits(ep, ranges, shots, media)
        X.srt(dict(session=ep["session"], ranges=ranges), f"{od}/captions/{ep['name']}.srt")
    res = [X.seq_format("fH", 1920, 1080)]
    for cam in PJ.cams:
        r = X.cam_rate(cam); code = X.RATE_CODE.get(r, "")
        res.append(f'<format id="{X.fmt_id(cam)}" name="FFVideoFormat{PJ.src_h}p{code}" frameDuration="{X.frame_dur(r)}" '
                   f'width="{PJ.src_w}" height="{PJ.src_h}" colorSpace="1-1-1 (Rec. 709)"/>')
    for p, a in X.assets.items():
        if a["kind"] == "audio":
            res.append(f'<asset id="{a["id"]}" name="{X.esc(a["name"])}" start="0s" duration="{X.TS(a["dur"], 48000)}" hasAudio="1" '
                       f'audioSources="1" audioChannels="2" audioRate="48000"><media-rep kind="original-media" src="{X.url(p)}"/></asset>')
        else:
            res.append(f'<asset id="{a["id"]}" name="{X.esc(a["name"])}" start="{X.TS(a["start"], a["rate"])}" duration="{X.TS(a["dur"], a["rate"])}" '
                       f'hasVideo="1" format="{X.fmt_id(a["cam"])}" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">'
                       f'<media-rep kind="original-media" src="{X.url(p)}"/></asset>')
    event = PJ.fcp.get("episodes_event", f"{PJ.name} Episodes")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.11"><resources>' + "".join(res) +
           f'</resources><library><event name="{X.esc(event)}">' + "".join(projects) + '</event></library></fcpxml>\n')
    os.makedirs(PJ.work, exist_ok=True)
    gap = f"{PJ.work}/episodes_gap_version.fcpxml"; open(gap, "w").write(xml)
    import restructure_fcpxml as RS
    RS.CLIPS = [dict(name=ep["name"]) for ep in eps]; RS.MEDIA = media
    RS.main(gap, f"{od}/{PJ.fcp.get('episodes_fcpxml_name', event + '.fcpxml')}")

if __name__ == "__main__":
    cmd = ARGS.pop(0) if ARGS else "plan"
    test = workers = None
    if "--test" in ARGS: i = ARGS.index("--test"); test = float(ARGS[i + 1]); del ARGS[i:i + 2]
    if "--workers" in ARGS: i = ARGS.index("--workers"); workers = int(ARGS[i + 1]); del ARGS[i:i + 2]
    eps = [e for e in EPISODES if not ARGS or e["name"] in ARGS]
    if cmd == "plan":
        for ep in eps:
            f = f"{PJ.dir}/shots/ep_{ep['name']}.json"
            if os.path.exists(f): os.remove(f)
            ranges, shots = load(ep); _, total, _ = out_map(ranges)
            c = Counter(s["kind"] + ("/" + s["who"] if s["kind"] == "cu" else "") for s in shots)
            L = [s["b"] - s["a"] for s in shots]
            print(f"{ep['name']}: {total / 60:.1f} min, {len(ranges)} ranges, {len(shots)} shots, avg {sum(L) / len(L):.1f}s, "
                  f"min {min(L):.1f}s, max {max(L):.1f}s, {dict(c)}")
    elif cmd == "render":
        for ep in eps: render(ep, test, workers or 4)
    elif cmd == "fcpxml":
        write_fcpxml(eps)
    else:
        raise SystemExit(__doc__)
