"""Per-show configuration for the Podcast Editing Pipeline.

Every script loads one project.json through cfg.get(). Where it looks, in order:
  1. `--project path/to/project.json` anywhere on the command line (removed from sys.argv)
  2. $PODCAST_PROJECT
  3. ./project.json (current directory)
  4. project.json next to the scripts

Relative paths in project.json resolve as follows:
  footage files / recorder folders  -> relative to "footage_root"
  "out_root"                        -> relative to "footage_root"
  outputs.*                         -> relative to "out_root"
  "work", picks/clips/edits/shots   -> relative to the folder that holds project.json
  "cache", "venv", "lut"            -> absolute or ~ paths (must NOT contain ':' - ffmpeg filter args break on it)

Time convention: every "offset" is the time on the session's REFERENCE camera clock (the camera whose
audio is transcribed) at which that file's own t=0 happens. file_time = ref_time - offset.
"""
import json, os, sys, glob, subprocess, shutil
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
_P = None


def _find(path=None):
    if path: return path
    if "--project" in sys.argv:
        i = sys.argv.index("--project"); p = sys.argv[i + 1]; del sys.argv[i:i + 2]; return p
    if os.environ.get("PODCAST_PROJECT"): return os.environ["PODCAST_PROJECT"]
    for d in (os.getcwd(), HERE):
        if os.path.exists(os.path.join(d, "project.json")): return os.path.join(d, "project.json")
    raise SystemExit("No project.json found. Pass --project <file>, set PODCAST_PROJECT, or run from the show folder.")


def get(path=None):
    global _P
    if _P is None: _P = Project(_find(path))
    return _P


def rate_frac(r):
    """'24000/1001' | 23.976 | '25' -> Fraction frames per second."""
    if isinstance(r, str) and "/" in r: a, b = r.split("/"); return Fraction(int(a), int(b))
    r = float(r)
    for n, d in ((24000, 1001), (30000, 1001), (60000, 1001)):
        if abs(r - n / d) < 0.005: return Fraction(n, d)
    return Fraction(r).limit_denominator(1000)


class Project:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.dir = os.path.dirname(self.path)
        self.c = c = json.load(open(self.path))
        ex = lambda p: os.path.expanduser(p)
        self.name = c["show"]["name"]
        self.slug = c["show"].get("slug") or "".join(ch if ch.isalnum() else "-" for ch in self.name.lower()).strip("-")
        self.root = ex(c["footage_root"])
        self.out = self.res(c.get("out_root", "REELS"))
        self.outputs = {k: os.path.join(self.out, v) for k, v in {
            "vertical": "", "horizontal": "horizontal", "quad": "web/quad", "fcpxml": "FCPXML", "fcp_media": "FCP MEDIA",
            **c.get("outputs", {})}.items()}
        self.work = self.local(c.get("work", "work"))
        self.cache = ex(c.get("cache", f"~/.cache/podcast-editing-pipeline/{self.slug}"))
        self.venv = ex(c.get("venv", "~/.venvs/podcast-editing-pipeline"))
        for k, v in (("cache", self.cache), ("venv", self.venv)):
            if ":" in v: raise SystemExit(f"project.json '{k}' path contains ':' ({v}); ffmpeg/venvs break on it. Use a ~ path.")
        self.personmask = ex(c.get("personmask", "~/.cache/podcast-editing-pipeline/bin/personmask"))
        self.fps_str = c.get("timeline_fps", "24000/1001")
        self.fps = rate_frac(self.fps_str)
        self.src_w, self.src_h = c.get("source_resolution", [3840, 2160])
        # cameras
        self.cams = c["cameras"]
        self.ref = next(k for k, v in self.cams.items() if v.get("reference"))
        self.cam_by_role = {}
        for k, v in self.cams.items(): self.cam_by_role.setdefault(v["role"], k)
        self.wide = self.cam_by_role.get("wide")
        # people: exactly guest / host / cohost (the review room's multicam angles)
        self.people = c["people"]
        for k in ("guest", "host", "cohost"):
            if k not in self.people: raise SystemExit(f"project.json people.{k} is missing (need guest, host, cohost)")
        self.recorder = c.get("recorder", {"mix": "Stereo Mix.wav"})
        self.sessions = {int(s["id"]): s for s in c["sessions"]}
        self.style = {"title_dur": 2.8, "headroom": 0.07, "min_turn": 0.5, "guest_cu": 5.0, "guest_split": 3.0,
                      "host_cu_max": 8.0, **c.get("style", {})}
        self.fix = c.get("transcript_fixes", {})
        self.fonts = {"title_file": "/Library/Fonts/BebasNeue-Regular.ttf", "caption_file": "/Library/Fonts/Inter-Medium.ttf",
                      "fcp_title_font": "Avenir Next Condensed", "fcp_title_face": "Heavy", **c.get("fonts", {})}
        self.audio_filter = c.get("audio_filter",
            "highpass=f=70,acompressor=threshold=-20dB:ratio=2.5:attack=10:release=200,loudnorm=I=-14:TP=-1.5:LRA=9")
        self.brand = c.get("brand", {})
        self.fcp = {"event": f"{self.name} Reels", "lut_keyword": "Apply Camera LUT", **c.get("fcp", {})}
        self.tx = {"model": "mlx-community/whisper-large-v3-turbo", "language": "en", **c.get("transcribe", {})}

    # ---------- paths ----------
    def res(self, p):
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.join(self.root, p)

    def local(self, p):
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.join(self.dir, p)

    def save(self):
        tmp = self.path + ".tmp"; json.dump(self.c, open(tmp, "w"), indent=1); os.replace(tmp, self.path)

    @property
    def lut(self):
        """The camera LUT, copied into the cache (ffmpeg's lut3d= cannot take paths with ':' or spaces safely)."""
        src = os.path.expanduser(self.c["lut"])
        if not os.path.isabs(src):  # relative: the show folder first, then the template (luts/...)
            src = next((q for q in (self.local(src), os.path.join(HERE, src)) if os.path.exists(q)), self.local(src))
        dst = os.path.join(self.cache, "camera_lut.cube")
        if not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(src):
            os.makedirs(self.cache, exist_ok=True); shutil.copyfile(src, dst)
        return dst

    # ---------- sessions / media ----------
    def ref_file(self, sid):
        return self.res(self.sessions[sid]["ref_file"])

    def media(self, sid, cam):
        """[(abs_path, offset, duration)] for a non-reference camera in a session (needs sync.py)."""
        if cam == self.ref: return [(self.ref_file(sid), 0.0, None)]
        out = []
        for m in self.sessions[sid].get("media", {}).get(cam, []):
            if m.get("offset") is None: raise SystemExit(f"session {sid} {cam}: no offset yet - run sync.py")
            out.append((self.res(m["file"]), float(m["offset"]), m.get("duration")))
        if not out: raise SystemExit(f"session {sid}: no {cam} files - list them in project.json or run sync.py")
        return out

    def takes(self, sid):
        """Recorder takes: [(abs_dir, offset, duration, index)]."""
        out = []
        for i, m in enumerate(self.sessions[sid].get("recorder", [])):
            if m.get("offset") is None: raise SystemExit(f"session {sid} recorder: no offset yet - run sync.py")
            out.append((self.res(m["dir"]), float(m["offset"]), float(m["duration"]), i))
        return out

    # ---------- work files ----------
    def tx_json(self, sid): return os.path.join(self.work, f"tx_{sid}.json")
    def tx_wav(self, sid): return os.path.join(self.work, f"tx_{sid}.wav")
    def env_file(self, key): return os.path.join(self.work, "env", key + ".raw")
    def env_ref(self, sid): return f"s{sid}_ref"
    def env_rec(self, sid, i):
        d = self.res(self.sessions[sid]["recorder"][i]["dir"])
        return self.env_media(os.path.join(d, self.recorder.get("mix", "Stereo Mix.wav")))
    def env_mic(self, sid, i, who): return f"s{sid}_rec{i}_{who}"
    def env_media(self, path):
        return "m_" + "".join(ch if ch.isalnum() else "_" for ch in os.path.relpath(path, self.root))[-80:]


def extract_env(src, dst, stream="0:a:0"):
    """1 kHz mono float32 copy of a file's audio: what sync + speaker detection correlate on."""
    if os.path.exists(dst) and os.path.getsize(dst) > 0: return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", stream, "-vn", "-ac", "1", "-ar", "1000",
                    "-f", "f32le", dst + ".part"], check=True)
    os.replace(dst + ".part", dst)
    return dst


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip())
