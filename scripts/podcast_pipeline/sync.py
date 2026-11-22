"""Find every camera / recorder offset automatically and write them into project.json.

Usage: sync.py project.json [--check] [--threshold 8]
  --check      print the result (and the diff against offsets already in project.json) without writing
  --threshold  minimum match strength (peak z-score) to accept a file into a session (default 8)

How it works (same signal the original pipeline synced on):
  1. Every file's audio is reduced to 1 kHz mono float32 (work/env/*.raw, cached).
  2. Coarse: a 100 Hz log-loudness envelope of each candidate file is cross-correlated (FFT, all lags)
     against each session's reference-camera envelope. The best peak's z-score says how sure the match is;
     each file goes to the session it matches best (if above --threshold), so you can list a whole card
     with a glob and let sync sort the files into sessions.
  3. Fine: at up to 7 points across the overlap, a +-80 ms search on the 1 kHz signal (same as render3.refine)
     pins the offset to ~1 ms. The median is stored; the spread is reported as clock drift.
Candidates: every file already listed in a session's "media"/"recorder", plus the "files" glob of each
camera and of the recorder (relative to footage_root; ** allowed). The reference camera is never moved:
offset = reference-clock time at which the file's t=0 happens (negative = it started rolling first).
After writing, the recorder mic tracks for each assigned take are extracted too (speaker detection)."""
import sys, os, glob, json, argparse
import numpy as np
from concurrent.futures import ThreadPoolExecutor

ap = argparse.ArgumentParser()
ap.add_argument("project"); ap.add_argument("--check", action="store_true"); ap.add_argument("--threshold", type=float, default=8.0)
A = ap.parse_args()
import cfg, prep
PJ = cfg.get(A.project)
os.makedirs(PJ.work + "/env", exist_ok=True)


def rel(p):
    return os.path.relpath(p, PJ.root) if os.path.abspath(p).startswith(os.path.abspath(PJ.root) + os.sep) else p


# ---------- candidates ----------
cands = {}  # cam -> set(abs path)   ("recorder" -> set(abs take dir))
for cam, c in PJ.cams.items():
    if cam == PJ.ref: continue
    s = set()
    if c.get("files"): s |= {p for p in glob.glob(PJ.res(c["files"]), recursive=True) if os.path.isfile(p)}
    for ses in PJ.sessions.values():
        s |= {PJ.res(m["file"]) for m in ses.get("media", {}).get(cam, [])}
    cands[cam] = s
mix = PJ.recorder.get("mix", "Stereo Mix.wav")
s = set()
if PJ.recorder.get("files"):
    s |= {os.path.dirname(p) for p in glob.glob(PJ.res(PJ.recorder["files"]), recursive=True)}
for ses in PJ.sessions.values():
    s |= {PJ.res(r["dir"]) for r in ses.get("recorder", [])}
cands["recorder"] = s

src_of = lambda cam, p: f"{p}/{mix}" if cam == "recorder" else p
jobs = [(PJ.ref_file(sid), PJ.env_file(PJ.env_ref(sid))) for sid in PJ.sessions]
jobs += [(src_of(cam, p), PJ.env_file(PJ.env_media(src_of(cam, p)))) for cam, ps in cands.items() for p in ps]
print(f"extracting audio for {len(jobs)} files (cached after the first run)...", flush=True)
with ThreadPoolExecutor(6) as ex: list(ex.map(lambda j: cfg.extract_env(*j), jobs))


# ---------- correlation ----------
def load(path): return np.fromfile(path, dtype=np.float32)

def env100(x):
    """100 Hz log-loudness, normalised: level-independent, so a camera scratch mic matches a clean mic mix."""
    n = len(x) // 10
    e = np.log(np.sqrt((x[:n * 10].reshape(n, 10) ** 2).mean(1)) + 1e-4)
    return (e - e.mean()) / (e.std() + 1e-9)

def coarse(r, s, min_overlap=3000):
    """Best lag (s starts at ref index k, 100 Hz) and its z-score over all lags with >= 30 s overlap."""
    N = 1 << int(np.ceil(np.log2(len(r) + len(s))))
    c = np.fft.irfft(np.fft.rfft(r, N) * np.conj(np.fft.rfft(s, N)), N)
    lags = np.concatenate([np.arange(0, len(r)), np.arange(-(len(s) - 1), 0)])
    vals = np.concatenate([c[:len(r)], c[N - (len(s) - 1):]])
    ov = np.minimum(len(r), lags + len(s)) - np.maximum(0, lags)
    ok = ov >= min(min_overlap, len(s) // 2)
    lags, vals, ov = lags[ok], vals[ok], ov[ok]
    v = vals / np.sqrt(ov)                       # don't favour big overlaps just for being big
    i = int(np.argmax(v))
    z = (v[i] - np.median(v)) / (v.std() + 1e-9)
    return lags[i] / 100.0, float(z)

def fine(r, s, off, win=20.0, search=0.08, points=7):
    """Pin the offset at several points across the overlap (1 kHz, abs-signal correlation like render3.refine)."""
    lo = max(0.0, off) + 5; hi = min(len(r) / 1000, off + len(s) / 1000) - win - 5
    if hi <= lo: return off, 0.0
    res = []
    n, k = int(win * 1000), int(search * 1000)
    for t in np.linspace(lo, hi, points):
        a = int(t * 1000); seg = np.abs(r[a:a + n])
        if seg.std() < 1e-5: continue
        seg = seg - seg.mean(); best, bo = -1e18, None; s0 = int(round((t - off) * 1000))
        for d in range(-k, k + 1):
            b = s0 + d
            if b < 0 or b + n > len(s): continue
            x = np.abs(s[b:b + n]); cc = float(np.dot(seg, x - x.mean()))
            if cc > best: best, bo = cc, off - d / 1000
        if bo is not None: res.append(bo)
    if not res: return off, 0.0
    return float(np.median(res)), float(max(res) - min(res))


refs = {sid: load(PJ.env_file(PJ.env_ref(sid))) for sid in PJ.sessions}
ref100 = {sid: env100(x) for sid, x in refs.items()}
found = {sid: {} for sid in PJ.sessions}   # sid -> cam -> [entry]
rows = []
for cam, ps in cands.items():
    for p in sorted(ps):
        x = load(PJ.env_file(PJ.env_media(src_of(cam, p)))); x100 = env100(x)
        scores = {sid: coarse(ref100[sid], x100) for sid in PJ.sessions}
        sid = max(scores, key=lambda k: scores[k][1]); off, z = scores[sid]
        if z < A.threshold:
            rows.append((cam, rel(p), None, None, z, None, "no session matched - skipped")); continue
        off, spread = fine(refs[sid], x, off)
        dur = cfg.probe_duration(src_of(cam, p))
        e = {"dir" if cam == "recorder" else "file": rel(p), "offset": round(off, 3), "duration": round(dur, 2), "match": round(z, 1)}
        found[sid].setdefault(cam, []).append(e)
        rows.append((cam, rel(p), sid, off, z, spread, ""))

# ---------- report ----------
def old_offset(sid, cam, p):
    ses = PJ.sessions[sid]
    lst = ses.get("recorder", []) if cam == "recorder" else ses.get("media", {}).get(cam, [])
    for m in lst:
        if PJ.res(m.get("dir") or m.get("file")) == PJ.res(p) and m.get("offset") is not None: return float(m["offset"])

print(f"\n{'camera':10s} {'session':>7s} {'offset s':>10s} {'was':>10s} {'diff ms':>8s} {'match':>6s} {'drift ms':>8s}  file")
for cam, p, sid, off, z, spread, note in sorted(rows, key=lambda r: (r[2] or 99, r[0], r[3] or 0)):
    if sid is None: print(f"{cam:10s} {'-':>7s} {'':>10s} {'':>10s} {'':>8s} {z:6.1f} {'':>8s}  {p}  ({note})"); continue
    was = old_offset(sid, cam, p)
    print(f"{cam:10s} {sid:7d} {off:10.3f} {('%.3f' % was) if was is not None else '':>10s} "
          f"{('%+.0f' % ((off - was) * 1000)) if was is not None else '':>8s} {z:6.1f} {spread * 1000:8.0f}  {p}")
for sid in PJ.sessions:
    for cam in [c for c in PJ.cams if c != PJ.ref] + ["recorder"]:
        if not found[sid].get(cam): print(f"WARNING: session {sid} has no {cam} match")

if A.check: print("\n--check: project.json not changed"); sys.exit(0)
for sid, ses in PJ.sessions.items():
    for cam, lst in found[sid].items():
        lst.sort(key=lambda e: e["offset"])
        if cam == "recorder": ses["recorder"] = lst
        else: ses.setdefault("media", {})[cam] = lst
PJ.c["sessions"] = [PJ.sessions[k] for k in sorted(PJ.sessions)]
PJ.save()
print(f"\nwrote offsets to {PJ.path}")
print("extracting mic tracks for speaker detection...", flush=True)
prep.run(prep.jobs(cfg.Project(PJ.path), tx_wav=False))
print("sync done")
