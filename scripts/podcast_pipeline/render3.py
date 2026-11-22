"""Podcast Editing Pipeline renderer (v3).
Usage: render3.py [--project project.json] clips.json [--fmt v,h,q] [name ...]
  v = vertical 1080x1920            -> outputs.vertical/<name>.mp4
  h = horizontal 1920x1080          -> outputs.horizontal/<name>.mp4
  q = multicam quad proxy 720x1280  -> outputs.quad/<name>.mp4 (2x2 of guest/host/cohost/wide)
Also writes the auto edit (output-time cuts) to <show>/shots/<name>.json.
If <show>/edits/<name>.json exists ({"cuts":[{"t":..,"angle":..}]}, saved from the review room's
Multicam tab), it replaces the auto edit.
All source times are on the session's reference-camera clock (the transcript's clock).
People keys are fixed: guest / host / cohost (+ the wide). Labels, cameras and mics come from project.json."""
import json, sys, os, subprocess, re, glob, shutil, numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import cfg

PJ = cfg.get()
P = PJ.dir                     # show folder: clips.json, edits/, shots/, look.json
S = PJ.work                    # transcripts + env/
CACHE = PJ.cache
OUT = PJ.out
PERSONMASK = PJ.personmask     # Apple Vision person segmentation CLI (tools/personmask.swift)
FPS = PJ.fps_str; FPSF = float(PJ.fps)
SW, SH = PJ.src_w, PJ.src_h    # source frame size all framing numbers are in (4K UHD by default)
FMT = {"v": dict(W=1080, H=1920), "h": dict(W=1920, H=1080)}
ST = PJ.style
_LUT = None
def LUT():
    global _LUT
    if _LUT is None: _LUT = PJ.lut
    return _LUT

def cam_of(who): return PJ.wide if who == "wide" else PJ.people[who]["camera"]
def grade(cam):
    """ffmpeg grade for one camera: the show LUT for log cameras, or the camera's own filter (level match)."""
    c = PJ.cams[cam]
    if c.get("grade", "lut") == "lut": return f"lut3d={LUT()}"
    return f"format=gbrp,{c['filter']}" if c.get("filter") else "null"

# ---------------- sync + speaker detection ----------------
_env = {}
def env(name):
    if name not in _env: _env[name] = np.fromfile(PJ.env_file(name), dtype=np.float32)
    return _env[name]

def refine(ref, src, approx, t, win=20.0, search=0.4):
    """Local re-sync around ref-time t: best offset within +-search s of approx (handles clock drift)."""
    r, s = env(ref), env(src)
    a = int(t * 1000); n = int(win * 1000); k = int(search * 1000)
    seg = np.abs(r[a:a + n]); best, bo = -1e9, approx; s0 = int((t - approx) * 1000)
    for d in range(-k, k + 1, 2):
        b = s0 + d
        if b < 0 or b + n > len(s): continue
        x = np.abs(s[b:b + n]); c = np.dot(seg - seg.mean(), x - x.mean())
        if c > best: best, bo = c, approx - d / 1000
    return bo

def rc_for(sess, t):
    """(take_dir, mix_env_key, take_index, offset) of the recorder take covering ref-time t."""
    for d, off, dur, i in PJ.takes(sess):
        if off <= t < off + dur: return d, PJ.env_rec(sess, i), i, off
    raise ValueError(f"no recorder audio at {t}")

_clip_off = {}
def set_clip(clip):
    """Re-sync each single-file camera locally at the clip start (clock drift over a long session),
    as the original pipeline did for the guest FX3. Call before planning/rendering a clip."""
    _clip_off.clear()
    sess, t = clip["session"], clip["ranges"][0][0] + 2
    for cam in PJ.cams:
        if cam in (PJ.ref, PJ.wide): continue
        files = PJ.media(sess, cam)
        if len(files) != 1: continue
        key = PJ.env_media(files[0][0])
        if os.path.exists(PJ.env_file(key)): _clip_off[(sess, cam)] = refine(PJ.env_ref(sess), key, files[0][1], t)

def media_for(sess, cam, t):
    """(file, offset) of the camera file covering ref-time t (cameras that split files, e.g. the Osmo)."""
    files = PJ.media(sess, cam)
    if len(files) == 1: return files[0][0], _clip_off.get((sess, cam), files[0][1])
    for f, off, dur in files:
        if off <= t < off + (dur or 1e9) - 0.5: return f, off
    raise ValueError(f"no {cam} footage at {t}")

def speaker_at(sess, a, b):
    d, envn, i, off = rc_for(sess, a); lv = []
    for who in ("guest", "host", "cohost"):
        x = env(PJ.env_mic(sess, i, who)); lo, hi = int((a - off) * 1000), int((b - off) * 1000)
        lv.append(20 * np.log10(float(np.sqrt(np.mean(x[lo:max(hi, lo + 20)] ** 2))) + 1e-12))  # raw level tested best
    return ("guest", "host", "cohost")[int(np.argmax(lv))]

_tr = {}
def words_in(sess, a, b):
    if sess not in _tr: _tr[sess] = json.load(open(PJ.tx_json(sess)))
    return [w for seg in _tr[sess]["segments"] for w in seg.get("words", [])
            if w["start"] >= a - 0.05 and w["end"] <= b + 0.05]

def speaker_turns(sess, a, b, min_turn=None, lead=0.12):
    """Shots for one source range cut word by word on who is speaking, plus the short reactions
    (turns under min_turn by someone else) that happened inside them."""
    min_turn = ST["min_turn"] if min_turn is None else min_turn
    seq = []
    for w in words_in(sess, a, b):
        ws, we = max(w["start"], a), min(w["end"], b)
        if we - ws < 0.05: continue
        who = speaker_at(sess, ws, we)
        if seq and seq[-1][2] == who: seq[-1][1] = we
        else: seq.append([ws, we, who])
    keep, reactions = [], []
    for t in seq:
        if keep and t[1] - t[0] < min_turn:
            if t[2] != keep[-1][2]: reactions.append(tuple(t))
            continue
        if keep and keep[-1][2] == t[2]: keep[-1][1] = t[1]; continue
        keep.append(t)
    if not keep: return [[a, b, "host"]], []
    shots = []
    for i, t in enumerate(keep):
        s0 = a if i == 0 else max(t[0] - lead, shots[-1][0] + 0.8)
        if shots: shots[-1][1] = s0
        shots.append([s0, b, t[2]])
    out = []
    for s in shots:
        if out and s[1] - s[0] < 0.8: out[-1][1] = s[1]
        else: out.append(s)
    if len(out) > 1 and out[0][1] - out[0][0] < 1.0: out[1][0] = out[0][0]; out.pop(0)
    return out, reactions

# ---------------- edit planning ----------------
def timeline(ranges):
    tl, acc = [], 0.0
    for a, b in ranges: tl.append((a, b, acc)); acc += b - a
    return tl, acc

def plan_auto(clip, td):
    """Source-time shots: dicts {a,b,kind:'cu'|'wide'|'split',who,low,punch,title}."""
    sess, ranges = clip["session"], clip["ranges"]
    turns, reacts = [], []
    for a, b in ranges:
        t, r = speaker_turns(sess, a, b); turns += t; reacts += r
    # the listener shown in splits: whichever of host / co-host talks most in this clip
    talk = {"host": 0.0, "cohost": 0.0}
    for a, b, w in turns:
        if w in talk: talk[w] += b - a
    listener = max(talk, key=talk.get) if max(talk.values()) > 0 else "host"
    hmax, cu = ST["host_cu_max"], ST["guest_cu"]
    shots = []
    for a, b, who in turns:
        if who != "guest":
            t = a; n = 0
            while b - t > hmax:
                shots.append(dict(a=t, b=t + cu, kind="cu", who=who, punch=n % 2 == 1)); t += cu; n += 1
            shots.append(dict(a=t, b=b, kind="cu", who=who, punch=n % 2 == 1)); continue
        # guest shot: reaction splits where host/co-host react, rhythm splits in long stretches
        rs = sorted([(max(a, r0 - 0.4), min(b, max(r1 + 1.0, r0 + 2.2)), rw) for r0, r1, rw in reacts
                     if a <= r0 < b and rw in ("host", "cohost")])
        merged = []
        for s0, s1, rw in rs:
            if merged and s0 < merged[-1][1] + 0.6: merged[-1][1] = max(merged[-1][1], s1)
            else: merged.append([s0, s1, rw])
        t = a
        for s0, s1, rw in merged:
            if s0 - t < 1.0: s0 = t
            if b - s1 < 1.0: s1 = b
            if s0 > t: shots += guest_run(t, s0, listener)
            shots.append(dict(a=s0, b=s1, kind="split", who="guest", low=rw)); t = s1
        if t < b: shots += guest_run(t, b, listener)
    # opener: depth title over the wide
    a0 = ranges[0][0]; out = [dict(a=a0, b=min(a0 + td, ranges[0][1]), kind="wide", who="wide", title=True)]
    for s in shots:
        if s["b"] <= out[0]["b"] + 0.05: continue
        if s["a"] < out[0]["b"]: s = dict(s, a=out[0]["b"])
        if s["b"] - s["a"] < 0.6: out[-1]["b"] = s["b"]; continue
        out.append(s)
    return out

def guest_run(a, b, listener):
    """A guest stretch: close-up, punch-in, split with the listener, repeat (5 s / 5 s / 3 s by default)."""
    cu, sp, hmax = ST["guest_cu"], ST["guest_split"], ST["host_cu_max"]
    out, t, n = [], a, 0
    while b - t > hmax:
        k = n % 3
        if k == 2: out.append(dict(a=t, b=t + sp, kind="split", who="guest", low=listener)); t += sp
        else: out.append(dict(a=t, b=t + cu, kind="cu", who="guest", punch=k == 1)); t += cu
        n += 1
    out.append(dict(a=t, b=b, kind="cu", who="guest", punch=n % 3 == 1))
    return out

def shot_angle(s):
    if s["kind"] == "split": return "split-host" if s["low"] == "host" else "split-cohost"
    return s["who"]

def to_cuts(shots, ranges):
    tl, _ = timeline(ranges); cuts = []
    for s in shots:
        for a, b, o in tl:
            if a - 0.01 <= s["a"] < b:
                t = round(o + s["a"] - a, 3); ang = shot_angle(s)
                if not cuts or cuts[-1]["angle"] != ang: cuts.append(dict(t=t, angle=ang))
                break
    if cuts: cuts[0]["t"] = 0.0
    return cuts

def from_cuts(cuts, ranges, td):
    """Rebuild source shots from an output-time cut list (multicam edits), split at range joins."""
    tl, total = timeline(ranges)
    cuts = sorted(cuts, key=lambda c: c["t"]); out = []
    for i, c in enumerate(cuts):
        t0, t1 = c["t"], (cuts[i + 1]["t"] if i + 1 < len(cuts) else total)
        if t1 - t0 < 0.05: continue
        for a, b, o in tl:
            lo, hi = max(t0, o), min(t1, o + b - a)
            if hi - lo < 0.05: continue
            sa, sb = a + lo - o, a + hi - o; ang = c["angle"]
            if ang.startswith("split"):
                out.append(dict(a=sa, b=sb, kind="split", who="guest", low="host" if ang == "split-host" else "cohost"))
            else:
                out.append(dict(a=sa, b=sb, kind="wide" if ang == "wide" else "cu", who=ang, punch=False))
    # the title always opens on the wide: it replaces the edit's first shot (up to td seconds)
    if out:
        f0 = out[0]; end = min(f0["b"], f0["a"] + td)
        title = dict(a=f0["a"], b=end, kind="wide", who="wide", title=True)
        if f0["b"] - end >= 0.6: out[0] = dict(f0, a=end); out.insert(0, title)
        else: out[0] = dict(title, b=f0["b"])
    return out

def shots_for(clip):
    """The edit for a clip: the team's multicam edit if saved, else the auto plan."""
    td = clip.get("title_dur", ST["title_dur"])
    ef = f"{P}/edits/{clip['name']}.json"
    return from_cuts(json.load(open(ef))["cuts"], clip["ranges"], td) if os.path.exists(ef) else plan_auto(clip, td)

# ---------------- team look (Color tab) ----------------
LOOK = ""
def look_filter(pr):
    """ffmpeg equivalent of the review room's SVG look (sRGB): gains -> contrast -> midtone gamma -> saturation."""
    ev, c, mid, sat = pr.get("ev", 0), pr.get("contrast", 1), pr.get("mid", 0), pr.get("sat", 1)
    temp, tint = pr.get("temp", 0), pr.get("tint", 0)
    g = 2 ** ev; gr, gg, gb = g * (1 + 0.10 * temp / 100), g * (1 - 0.08 * tint / 100), g * (1 - 0.10 * temp / 100)
    gam = 2 ** (-mid / 100); off = 0.5 * (1 - c)
    e = lambda k: f"clip(pow(clip(val/maxval*{k * c:.5f}+{off:.5f}\\,0\\,1)\\,{gam:.5f})*maxval\\,0\\,maxval)"
    r, gL, b = 0.2126, 0.7152, 0.0722
    m = [r + (1 - r) * sat, gL - gL * sat, b - b * sat, r - r * sat, gL + (1 - gL) * sat, b - b * sat, r - r * sat, gL - gL * sat, b + (1 - b) * sat]
    return (f",format=gbrp,lutrgb=r='{e(gr)}':g='{e(gg)}':b='{e(gb)}'"
            f",colorchannelmixer=rr={m[0]:.5f}:rg={m[1]:.5f}:rb={m[2]:.5f}:gr={m[3]:.5f}:gg={m[4]:.5f}:gb={m[5]:.5f}:br={m[6]:.5f}:bg={m[7]:.5f}:bb={m[8]:.5f}")

def set_look(name):
    """<show>/look.json: {"all": params, "<reel id>": params} - the look the team selected in the Color tab."""
    global LOOK
    LOOK = ""
    f = P + "/look.json"
    if os.path.exists(f):
        lk = json.load(open(f)); pr = lk.get(name) or lk.get("all")
        if pr: LOOK = look_filter(pr)

# ---------------- framing ----------------
_fc = [cv2.CascadeClassifier(cv2.data.haarcascades + n) for n in
       ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")]
_facec = {}
def face_c(f, ss, dur, x0, y0):
    """Median face centre (source px) near the default 1080-wide window at x0; falls back to defaults."""
    key = (f, round(ss, 1), round(dur, 1), x0)
    if key in _facec: return _facec[key]
    q4 = SW / 960; xs, ys = [], []
    for q in (0.2, 0.5, 0.8):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ss + dur * q:.2f}", "-i", f, "-map", "0:v:0",
                            "-frames:v", "1", "-vf", "scale=960:540,format=gray", "-f", "rawvideo", "-"], capture_output=True)
        if len(r.stdout) != 960 * 540: continue
        img = np.frombuffer(r.stdout, np.uint8).reshape(540, 960)
        lo, hi = max(0, int((x0 - 350) / q4)), min(960, int((x0 + 1080 + 350) / q4))
        roi = img[:330, lo:hi]; best = None
        for c in _fc:
            for flip in (False, True):
                im = roi[:, ::-1] if flip else roi
                for (x, y, w, h) in c.detectMultiScale(im, 1.1, 5, minSize=(40, 40)):
                    cx = (im.shape[1] - (x + w / 2)) if flip else (x + w / 2)
                    if best is None or w > best[2]: best = (cx + lo, y + h / 2, w)
        if best: xs.append(best[0] * q4); ys.append(best[1] * q4)
    res = (float(np.median(xs)), float(np.median(ys))) if xs else (x0 + 540.0, float(y0))
    _facec[key] = res; return res

def clampi(v, lo, hi): return int(min(max(v, lo), hi))

def source(sess, who, a):
    """(file, file-time) for a person's camera at ref-time a."""
    f, off = media_for(sess, cam_of(who), a)
    return f, a - off

_headc = {}
def head_top(f, ss, dur, xl, xr):
    """Highest point of any person inside columns xl..xr (source px) over 5 frames of the shot, from the Vision matte."""
    key = (f, round(ss, 2), round(dur, 2), xl, xr)
    if key in _headc: return _headc[key]
    q4 = SW / 960
    d = f"{CACHE}/head/{abs(hash(key))}"; shutil.rmtree(d, ignore_errors=True); os.makedirs(d + "/f")
    for i, q in enumerate((0.05, 0.27, 0.5, 0.73, 0.95)):
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ss + dur * q:.2f}", "-i", f, "-map", "0:v:0", "-frames:v", "1",
                        "-vf", "scale=960:540", f"{d}/f/{i}.png"], capture_output=True)
    subprocess.run([PERSONMASK, d + "/f", d + "/m"], capture_output=True)
    tops = []
    lo, hi = max(0, int(xl / q4)), min(960, int(xr / q4))
    for p in glob.glob(d + "/m/*.png"):
        m = np.array(Image.open(p).convert("L"))[:, lo:hi] > 128
        rows = np.where(m.sum(1) >= max(3, int(0.012 * (hi - lo))))[0]
        if len(rows): tops.append(int(rows.min() * q4))
    shutil.rmtree(d, ignore_errors=True)
    res = min(tops) if tops else None
    _headc[key] = res; return res

def person_crop(sess, who, a, dur, cw, ch, head_frac):
    """Crop (x,y) of size cw x ch in source px around the person: centred on the face, top set from the head matte."""
    f, ss = source(sess, who, a)
    pp = PJ.people[who]
    x0 = pp.get("face_x_by_session", {}).get(str(sess), pp["face_x"])
    cx, cy = face_c(f, ss, dur, x0, pp["face_y"])
    x = clampi(cx - cw / 2, 0, SW - cw)
    ht = head_top(f, ss, dur, x, x + cw)
    y = clampi(ht - ST["headroom"] * ch, 0, SH - ch) if ht is not None else clampi(cy - ch * head_frac, 0, SH - ch)
    return f, ss, x, y

def cu_size(who, fmt):
    """Close-up crop (cw, ch, head_frac) in source px. Vertical: a 1080x1920 slice. Horizontal: full frame for a
    single-person camera ("h_closeup": "full"), a 2x punch into one person for a two-shot camera ("half")."""
    if fmt == "v": return 1080, 1920, 0.20
    if PJ.cams[cam_of(who)].get("h_closeup", "full" if PJ.cams[cam_of(who)]["role"] == "guest" else "half") == "full":
        return SW, SH, 0.24
    return SW // 2, SH // 2, 0.36

def split_size(fmt):
    return (1620, 1440, 0.30) if fmt == "v" else (1440, 1620, 0.28)

def wide_crop(fmt):
    if fmt == "v":
        x, y = PJ.cams[PJ.wide].get("vertical_crop", [(SW - 1080) // 2, (SH - 1920) // 2 if SH > 1920 else 0])
        return x, y, 1080, 1920
    return 0, 0, SW, SH

def shot_filter(s, sess, fmt, W, H, graded=True):
    """(inputs, filter_complex producing [v]) for one shot. graded=False leaves log cameras ungraded
    (the 16x9 FCP split clips take the Camera LUT in Final Cut instead)."""
    dur = s["b"] - s["a"]
    g = lambda who: (grade(cam_of(who)) + LOOK) if graded or PJ.cams[cam_of(who)].get("grade", "lut") != "lut" else "null"
    if s["kind"] == "wide":
        f, off = media_for(sess, PJ.wide, s["a"])
        x, y, cw, ch = wide_crop(fmt)
        crop = f"crop={cw}:{ch}:{x}:{y}" if fmt == "v" else f"scale={W}:{H}"
        return [(f, s["a"] - off)], f"[0:v]{crop},{g('wide')},scale={W}:{H}:flags=lanczos,setsar=1[v]"
    if s["kind"] == "cu":
        cw, ch, hf = cu_size(s["who"], fmt)
        f, ss, x, y = person_crop(sess, s["who"], s["a"], dur, cw, ch, hf)
        vf = f"crop={cw}:{ch}:{x}:{y}"
        if s.get("punch"): vf += f",crop=iw*0.87:ih*0.87:iw*0.065:0"  # anchored at the top: never trims the head
        return [(f, ss)], f"[0:v]{vf},{g(s['who'])},scale={W}:{H}:flags=lanczos,setsar=1[v]"
    # split: guest + listener; vertical = stacked (guest on top), horizontal = side by side (listener left)
    cw, ch, hf = split_size(fmt)
    pw, ph = (W, H // 2) if fmt == "v" else (W // 2, H)
    ins, parts = [], []
    for i, who in enumerate(("guest", s["low"]) if fmt == "v" else (s["low"], "guest")):
        f, ss, x, y = person_crop(sess, who, s["a"], dur, cw, ch, hf)
        ins.append((f, ss)); parts.append(f"[{i}:v]crop={cw}:{ch}:{x}:{y},{g(who)},scale={pw}:{ph}:flags=lanczos,setsar=1[p{i}]")
    stack = "vstack" if fmt == "v" else "hstack"
    return ins, ";".join(parts) + f";[p0][p1]{stack}=inputs=2,drawbox=x={'0' if fmt == 'v' else 'iw/2-2'}:y={'ih/2-2' if fmt == 'v' else '0'}:w={'iw' if fmt == 'v' else '4'}:h={'4' if fmt == 'v' else 'ih'}:color=black@0.9:t=fill[v]"

def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: print(" ".join(map(str, cmd))[:3000]); print(r.stderr[-2000:]); raise SystemExit(1)

def encode_shot(ins, fc, dur, out):
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for f, ss in ins: cmd += ["-ss", f"{ss:.3f}", "-t", f"{dur + 0.2:.3f}", "-i", f]
    sh(cmd + ["-filter_complex", fc.replace("[v]", f",fps={FPS}[v]") if fc.endswith("[v]") else fc,
              "-map", "[v]", "-t", f"{dur:.3f}", "-an", "-c:v", "prores_videotoolbox", "-profile:v", "hq", out])

# ---------------- text ----------------
TITLE_FONT = PJ.fonts["title_file"]
CAP_FONT_PATH = PJ.fonts["caption_file"]
INK_DARK = tuple(PJ.brand.get("title_ink_dark", [22, 26, 30]))
INK_LIGHT = tuple(PJ.brand.get("title_ink_light", [255, 255, 255]))

def wrap(d, words, F, maxw):
    """Fewest lines that fit maxw, then the break points that make the lines most even."""
    import itertools
    L = lambda ws: d.textlength(" ".join(ws), font=F)
    for k in range(1, len(words) + 1):
        best = None
        for cuts in itertools.combinations(range(1, len(words)), k - 1):
            b = (0,) + cuts + (len(words),); ls = [words[b[i]:b[i + 1]] for i in range(k)]
            wmax = max(L(x) for x in ls)
            if wmax <= maxw and (best is None or wmax < best[0]): best = (wmax, [" ".join(x) for x in ls])
        if best: return best[1]
    return [" ".join(words)]

def depth_title(shot_mov, title, fmt, W, H, td, tmp):
    """Composite the title BEHIND the people (Vision person matte), fading out at the end."""
    fr = f"{tmp}/tf"; mk = f"{tmp}/tm"
    for d in (fr, mk): shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    sh(["ffmpeg", "-v", "error", "-y", "-i", shot_mov, f"{fr}/%04d.png"])
    sh([PERSONMASK, fr, mk])
    frames = sorted(glob.glob(f"{fr}/*.png"))
    first = Image.open(frames[0]).convert("RGB"); m0 = np.array(Image.open(f"{mk}/{os.path.basename(frames[0])}").convert("L")) > 128
    rows = np.where(m0.any(1))[0]; head_top = int(rows.min()) if len(rows) else H // 2
    size, maxw = (215, 960) if fmt == "v" else (250, 1700)
    d0 = ImageDraw.Draw(first)
    while True:
        F = ImageFont.truetype(TITLE_FONT, size); lines = wrap(d0, title.upper().split(), F, maxw)
        fits = max(d0.textlength(l, font=F) for l in lines) <= maxw  # one long word can be wider than the frame
        if (len(lines) <= (4 if fmt == "v" else 2) and fits) or size <= 110: break
        size -= 15
    lh = int(size * 0.88); block = lh * len(lines)
    y0 = max(int(head_top + 0.20 * size) - block, int(H * 0.06))
    region = np.array(first.convert("L"))[y0:y0 + block, :]
    ink = INK_DARK if region.size and region.mean() > 140 else INK_LIGHT
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(layer); y = y0
    for ln in lines:
        w = d.textlength(ln, font=F); d.text(((W - w) / 2, y), ln, font=F, fill=ink + (255,)); y += lh
    n = len(frames); fade_n = max(1, int(0.3 * FPSF))
    for i, p in enumerate(frames):
        fimg = Image.open(p).convert("RGBA"); m = Image.open(f"{mk}/{os.path.basename(p)}").convert("L").filter(ImageFilter.GaussianBlur(1.2))
        a = 1.0 if i < n - fade_n else max(0.0, (n - i) / fade_n)
        lay = layer if a >= 1 else Image.fromarray((np.array(layer) * np.array([1, 1, 1, a])).astype(np.uint8))
        comp = Image.alpha_composite(fimg, lay); comp.paste(fimg, (0, 0), m)
        comp.convert("RGB").save(p)
    out = shot_mov.replace(".mov", "_t.mov")
    sh(["ffmpeg", "-v", "error", "-y", "-framerate", FPS, "-i", f"{fr}/%04d.png", "-c:v", "prores_videotoolbox", "-profile:v", "hq", "-pix_fmt", "yuv422p10le", out])
    return out

def cap_png(text, path, W, H, fmt):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text:
        F = ImageFont.truetype(CAP_FONT_PATH, 46 if fmt == "v" else 50)
        d = ImageDraw.Draw(im); tw = d.textlength(text, font=F)
        x, y = (W - tw) / 2, int(H * (0.60 if fmt == "v" else 0.83))
        shd = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ds = ImageDraw.Draw(shd)
        ds.text((x, y + 3), text, font=F, fill=(0, 0, 0, 255), stroke_width=6, stroke_fill=(0, 0, 0, 255))
        shd = shd.filter(ImageFilter.GaussianBlur(9)); shd.putalpha(shd.getchannel("A").point(lambda v: int(v * 0.75)))
        im = Image.alpha_composite(im, shd); d = ImageDraw.Draw(im)
        d.text((x, y), text, font=F, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 140))
    im.save(path)

FIX = PJ.fix   # per-show transcript corrections (names, jargon Whisper gets wrong)
def chunk_words(words, maxc=30, maxw=6):
    chunks, cur = [], []
    for w in words:
        cur.append(w); txt = "".join(x["word"] for x in cur).strip()
        if len(txt) >= maxc or len(cur) >= maxw or re.search(r"[.?!,]$", w["word"].strip()): chunks.append(cur); cur = []
    if cur: chunks.append(cur)
    res = []
    for c in chunks:
        t = "".join(x["word"] for x in c).strip()
        for k, v in FIX.items(): t = t.replace(k, v)
        res.append((t, c[0]["start"], c[-1]["end"]))
    return res

# ---------------- audio ----------------
def build_audio(clip, tmp):
    out = f"{tmp}/audio.wav"
    if os.path.exists(out): return out
    sess, parts = clip["session"], []
    mix = PJ.recorder.get("mix", "Stereo Mix.wav")
    for i, (a, b) in enumerate(clip["ranges"]):
        d, envn, _, off = rc_for(sess, a); off = refine(PJ.env_ref(sess), envn, off, a + 1)
        o = f"{tmp}/a{i:02d}.wav"
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{a - off:.3f}", "-i", f"{d}/{mix}", "-t", f"{b - a:.3f}",
            "-af", f"afade=in:d=0.02,afade=out:st={b - a - 0.03:.3f}:d=0.03", o]); parts.append(o)
    with open(f"{tmp}/a.txt", "w") as fh:
        for p in parts: fh.write(f"file '{p}'\n")
    sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{tmp}/a.txt",
        "-af", PJ.audio_filter, "-ar", "48000", out])
    return out

# ---------------- render ----------------
def render(clip, fmts):
    sess, name, ranges = clip["session"], clip["name"], clip["ranges"]
    td = clip.get("title_dur", ST["title_dur"])
    tmp = f"{CACHE}/build3/{name}"; shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    tl, total = timeline(ranges)
    def to_out(t):
        for a, b, o in tl:
            if a - 0.05 <= t <= b + 0.05: return o + min(max(t, a), b) - a
    set_look(name); set_clip(clip)
    auto = plan_auto(clip, td)
    os.makedirs(P + "/shots", exist_ok=True)
    json.dump(to_cuts(auto, ranges), open(f"{P}/shots/{name}.json", "w"))
    shots = shots_for(clip)
    audio = build_audio(clip, tmp)
    words = [w for a, b in ranges for w in words_in(sess, a, b)]
    chunks = chunk_words(words)
    for fmt in fmts:
        if fmt == "q": quad(clip, audio, tmp); continue
        W, H = FMT[fmt]["W"], FMT[fmt]["H"]; ft = f"{tmp}/{fmt}"; os.makedirs(ft)
        lst = open(f"{ft}/v.txt", "w")
        for i, s in enumerate(shots):
            ins, fc = shot_filter(s, sess, fmt, W, H); o = f"{ft}/v{i:03d}.mov"
            encode_shot(ins, fc, s["b"] - s["a"], o)
            if s.get("title"): o = depth_title(o, clip["title"], fmt, W, H, td, ft)
            lst.write(f"file '{o}'\n")
        lst.close()
        sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{ft}/v.txt", "-c", "copy", f"{ft}/video.mov"])
        blank = f"{ft}/blank.png"; cap_png("", blank, W, H, fmt); seq, t = [], 0.0
        for j, (txt, a, b) in enumerate(chunks):
            oa, ob = to_out(a), to_out(b)
            if oa is None or ob is None: continue
            if oa < td: continue  # the depth title carries the opening line
            if oa > t: seq.append((blank, oa - t))
            p = f"{ft}/c{j:03d}.png"; cap_png(txt, p, W, H, fmt)
            nxt = to_out(chunks[j + 1][1]) if j + 1 < len(chunks) else None
            end = nxt if (nxt is not None and nxt - ob < 0.6) else ob + 0.15
            seq.append((p, max(end - oa, 0.05))); t = oa + max(end - oa, 0.05)
        if t < total: seq.append((blank, total - t))
        with open(f"{ft}/c.txt", "w") as fh:
            for p, d in seq: fh.write(f"file '{p}'\nduration {d:.3f}\n")
            fh.write(f"file '{seq[-1][0]}'\n")
        od = PJ.outputs["vertical" if fmt == "v" else "horizontal"]; os.makedirs(od, exist_ok=True)
        sh(["ffmpeg", "-v", "error", "-y", "-i", f"{ft}/video.mov", "-f", "concat", "-safe", "0", "-i", f"{ft}/c.txt", "-i", audio,
            "-filter_complex", "[0:v][1:v]overlay=0:0:eof_action=pass,format=yuv420p[v]", "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-r", FPS,
            "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-shortest", f"{od}/{name}.mp4"])
        print(f"{name} [{fmt}]: {total:.1f}s, {len(shots)} shots, {sum(1 for s in shots if s['kind']=='split')} splits")
        shutil.rmtree(ft, ignore_errors=True)  # free the intermediates for this format right away

def quad(clip, audio, tmp):
    """2x2 multicam proxy in output time: guest | host / co-host | wide, 360x640 each."""
    sess, name, ranges = clip["session"], clip["name"], clip["ranges"]; qt = f"{tmp}/q"; os.makedirs(qt)
    feeds = []
    for k, who in enumerate(("guest", "host", "cohost", "wide")):
        lst = open(f"{qt}/f{k}.txt", "w")
        for i, (a, b) in enumerate(ranges):
            s = dict(a=a, b=b, kind="wide" if who == "wide" else "cu", who=who)
            ins, fc = shot_filter(s, sess, "v", 360, 640); o = f"{qt}/f{k}_{i}.mov"
            encode_shot(ins, fc, b - a, o); lst.write(f"file '{o}'\n")
        lst.close(); feeds.append(f"{qt}/f{k}.txt")
    od = PJ.outputs["quad"]; os.makedirs(od, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for f in feeds: cmd += ["-f", "concat", "-safe", "0", "-i", f]
    sh(cmd + ["-i", audio, "-filter_complex", "[0:v][1:v][2:v][3:v]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0,format=yuv420p[v]",
              "-map", "[v]", "-map", "4:a", "-c:v", "libx264", "-preset", "medium", "-b:v", "1100k", "-maxrate", "1400k", "-bufsize", "2800k",
              "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-shortest", f"{od}/{name}.mp4"])
    print(f"{name} [q]: quad proxy")
    shutil.rmtree(qt, ignore_errors=True)

if __name__ == "__main__":
    args = sys.argv[1:]; fmts = ["v"]
    if "--fmt" in args:
        i = args.index("--fmt"); fmts = args[i + 1].split(","); del args[i:i + 2]
    clips = json.load(open(args[0] if args else f"{P}/clips.json")); want = set(args[1:])
    for c in clips:
        if not want or c["name"] in want:
            render(c, fmts); shutil.rmtree(f"{CACHE}/build3/{c['name']}", ignore_errors=True)
