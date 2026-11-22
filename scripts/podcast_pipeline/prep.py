"""Extract the audio every later step works on (reads footage, never writes next to it).
Usage: prep.py [--project project.json] [--no-tx-wav]
  work/tx_<sid>.wav          16 kHz mono of the reference camera (what Whisper transcribes)
  work/env/s<sid>_ref.raw    1 kHz float32 of the reference camera (the sync clock)
  work/env/m_<file>.raw      1 kHz float32 of every other camera file listed in project.json / found by sync.py
  work/env/s<sid>_rec<i>_mix.raw and _<person>.raw   recorder stereo mix + each person's mic track
Already-extracted files are skipped. sync.py calls the same extraction for the files it needs."""
import os, sys, subprocess
from concurrent.futures import ThreadPoolExecutor
import cfg


def jobs(PJ, tx_wav=True):
    J = []
    for sid in PJ.sessions:
        ref = PJ.ref_file(sid)
        if tx_wav and not os.path.exists(PJ.tx_wav(sid)):
            J.append(("ffmpeg", ["ffmpeg", "-v", "error", "-y", "-i", ref, "-vn", "-ac", "1", "-ar", "16000", PJ.tx_wav(sid)]))
        J.append((ref, PJ.env_file(PJ.env_ref(sid))))
        for cam, ms in PJ.sessions[sid].get("media", {}).items():
            for m in ms: J.append((PJ.res(m["file"]), PJ.env_file(PJ.env_media(PJ.res(m["file"])))))
        for i, r in enumerate(PJ.sessions[sid].get("recorder", [])):
            d = PJ.res(r["dir"])
            J.append((f"{d}/{PJ.recorder.get('mix', 'Stereo Mix.wav')}", PJ.env_file(PJ.env_rec(sid, i))))
            for who, p in PJ.people.items():
                if p.get("mic"): J.append((f"{d}/{p['mic']}", PJ.env_file(PJ.env_mic(sid, i, who))))
    return J


def run(J, workers=6):
    def one(j):
        if j[0] == "ffmpeg": subprocess.run(j[1], check=True); return j[1][-1]
        return cfg.extract_env(*j)
    with ThreadPoolExecutor(workers) as ex:
        for out in ex.map(one, J): print("ok", os.path.basename(out), flush=True)


if __name__ == "__main__":
    PJ = cfg.get()
    os.makedirs(PJ.work + "/env", exist_ok=True)
    run(jobs(PJ, "--no-tx-wav" not in sys.argv))
    print("prep done")
