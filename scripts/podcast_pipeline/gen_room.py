"""Build <show>/review-room.html from review-room.template.html (publish it as a claude.ai Artifact).
Usage: gen_room.py [--project project.json] [clips.json]
Inputs: clips.json, work/tx_<session>.json, <show>/asset_urls.json ({id: {"v","h","q"}} or legacy {id: url}),
<show>/shots/<id>.json ([{"t": output_seconds, "angle": ...}], written by render3.py, optional).
Show name, subtitle, people labels and brand colors come from project.json."""
import json, os, sys, html
import cfg

PJ = cfg.get()
T = os.path.dirname(os.path.abspath(__file__))
D = PJ.dir
clips = json.load(open(sys.argv[1] if len(sys.argv) > 1 else f"{D}/clips.json"))
guest = {sid: s.get("guest", "Guest") for sid, s in PJ.sessions.items()}
mmss = lambda t: f"{int(t//60)}:{int(t%60):02d}"
ANGLES = {"guest", "host", "cohost", "wide", "split-host", "split-cohost"}
items, tx = [], {}
for c in clips:
    if c["session"] not in tx: tx[c["session"]] = json.load(open(PJ.tx_json(c["session"])))
    words = [w["word"] for s in tx[c["session"]]["segments"] for w in s.get("words", []) for a, b in c["ranges"] if a-0.05 <= w["start"] and w["end"] <= b+0.05]
    sp = f"{D}/shots/{c['name']}.json"
    shots = [{"t": 0, "angle": "guest"}]
    if os.path.exists(sp):
        raw = [s for s in json.load(open(sp)) if s.get("angle") in ANGLES]
        if raw: shots = sorted(({"t": round(float(s["t"]), 2), "angle": s["angle"]} for s in raw), key=lambda s: s["t"])
    items.append(dict(id=c["name"], title=c["title"], guest=guest[c["session"]], session=c["session"],
        dur=round(sum(b-a for a, b in c["ranges"]), 1), src=" + ".join(f"{mmss(a)}–{mmss(b)}" for a, b in c["ranges"]),
        text="".join(words).strip(), shots=shots))
au = f"{D}/asset_urls.json"
src = json.load(open(au)) if os.path.exists(au) else {}
src = {k: ({"v": v} if isinstance(v, str) else {f: u for f, u in v.items() if f in ("v", "h", "q") and u}) for k, v in src.items()}

def css(theme):
    m = {"accent": "--accent", "guest": "--a-guest", "host": "--a-host", "cohost": "--a-cohost", "wide": "--a-wide",
         "splithost": "--a-splithost", "splitcohost": "--a-splitcohost"}
    return "".join(f"{m[k]}:{v};" for k, v in PJ.brand.get(theme, {}).items() if k in m)
lt, dk = css("light"), css("dark")
brand = ""
if lt: brand += f":root{{{lt}}}\n"
if dk: brand += f'@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{{dk}}}}}\n:root[data-theme="dark"]{{{dk}}}\n'
guests = sorted(set(guest[c["session"]] for c in clips))
sub = PJ.c["show"].get("subtitle", f"{len(clips)} cuts") + " · " + " & ".join(guests)
labels = {k: PJ.people[k]["label"] for k in ("guest", "host", "cohost")}
j = lambda x: json.dumps(x, ensure_ascii=False).replace("</", "<\\/")
t = open(f"{T}/review-room.template.html").read()
t = (t.replace("__DATA__", j(items)).replace("__SRC__", j(src)).replace("__LABELS__", j(labels))
      .replace("/*__BRAND_CSS__*/", brand).replace("__SHOW__", html.escape(PJ.name)).replace("__SUB__", html.escape(sub)))
open(f"{D}/review-room.html", "w").write(t); print("wrote", f"{D}/review-room.html", len(t))
