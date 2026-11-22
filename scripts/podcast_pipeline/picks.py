"""Topic picks -> clips.json.
Usage: picks.py [--project project.json] [picks.json] [clips.json]
picks.json (in the show folder) is the editorial choice, written by hand or by Claude after reading the
transcripts. Each pick:
  {"name": "01_student_loan_surprise", "session": 1, "title": "The student loan surprise",
   "ranges": [[470, "student loans came out", "masterful plan"], ...]}
Each range = [approx ref-clock second, first words, last words]. The phrases are matched word by word
against the session transcript (searching from 40 s before the approx time), so they must be verbatim
Whisper words (punctuation and case don't matter). Output ranges get 0.12 s lead-in and 0.3 s tail.
Optional per pick: "title_dur" (seconds of title over the wide)."""
import json, re, sys
import cfg

PJ = cfg.get()
a = sys.argv[1:]
src = a[0] if a else f"{PJ.dir}/picks.json"
dst = a[1] if len(a) > 1 else f"{PJ.dir}/clips.json"

def norm(s): return re.sub(r"[^a-z0-9$+']", "", s.lower().replace("’", "'"))
out, tx = [], {}
for p in json.load(open(src)):
    name, sess, title = p["name"], int(p["session"]), p["title"]
    if sess not in tx: tx[sess] = [w for s in json.load(open(PJ.tx_json(sess)))["segments"] for w in s.get("words", [])]
    W = tx[sess]
    toks = [norm(w["word"]) for w in W]
    def find(ph, t0):
        q = [x for x in (norm(x) for x in ph.split()) if x]
        for i in range(len(W)):
            if W[i]["start"] < t0 - 40: continue
            j = i; k = 0
            while k < len(q) and j < len(toks):
                if toks[j] == "": j += 1; continue
                if toks[j] != q[k]: break
                j += 1; k += 1
            if k == len(q): return i, j - 1
        raise SystemExit(f"{name}: not found '{ph}' near {t0}")
    ranges = []
    for t0, ph_a, ph_b in p["ranges"]:
        i, _ = find(ph_a, t0); _, j = find(ph_b, W[i]["start"])
        ranges.append([round(W[i]["start"] - 0.12, 2), round(W[j]["end"] + 0.3, 2)])
    dur = sum(b - a for a, b in ranges)
    print(f"{name:36s} {dur:5.1f}s {ranges}")
    c = dict(name=name, session=sess, title=title, ranges=ranges)
    if "title_dur" in p: c["title_dur"] = p["title_dur"]
    out.append(c)
json.dump(out, open(dst, "w"), indent=1)
print("wrote", dst)
