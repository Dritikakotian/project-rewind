"""Build the Project Rewind demo video.

The story is real: a scratch git project is driven through the real engine (rewind.py), and every panel and graph
you see is the real UI (panel.html, graph_template.html) rendered with that engine's real output. Only the
editor/terminal chrome around them is a mock.

    python demo/make_demo.py <work-dir> <path-to-ffmpeg>      ->  docs/project-rewind-demo.mp4
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = Path(sys.argv[1]).resolve()
FFMPEG = sys.argv[2]
OUT = ROOT / "docs"
EDGE = next(p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p))
REWIND = ROOT / "rewind.py"
repo = WORK / "checkout-service"
env = {**os.environ, "REWIND_HOME": str(WORK / "home")}
def rmtree(path):
    """git marks its object files read-only; clear that so Windows can delete them."""
    def fix(func, p, _exc):
        os.chmod(p, 0o700)
        func(p)
    shutil.rmtree(path, onexc=fix) if Path(path).exists() else None


rmtree(WORK / "home")
rmtree(repo)
repo.mkdir(parents=True)


def rw(*args, stdin=None):
    r = subprocess.run([sys.executable, "-I", str(REWIND), "--project", str(repo), *args], input=stdin,
                       capture_output=True, text=True, encoding="utf-8", env=env)
    return r.stdout


def git(*args, name="Dritika Kotian"):
    subprocess.run(["git", "-C", str(repo), "-c", f"user.name={name}", "-c", "user.email=dev@example.com", *args],
                   check=True, capture_output=True)


def state():
    return json.loads(rw("state").strip().splitlines()[-1])


def notes(out):
    return [l.replace("[rewind] ", "") for l in out.splitlines() if l.startswith("[rewind]")]


# ------------------------------------------------------------------ code versions shown in the editor
V0 = '''import requests

GATEWAY = "https://pay.example.com/charge"


def charge(order):
    return requests.post(GATEWAY, json=order)
'''
V1 = '''import requests

GATEWAY = "https://pay.example.com/charge"


# TODO: add retry when the gateway times out
def charge(order):
    return requests.post(GATEWAY, json=order, timeout=30)
'''
V2 = V1.replace("timeout=30", "timeout=60")
V3 = '''import time
import requests

GATEWAY = "https://pay.example.com/charge"


def charge(order, attempts=4):
    for i in range(attempts):
        try:
            return requests.post(GATEWAY, json=order, timeout=30)
        except requests.Timeout:
            time.sleep(2 ** i)  # back off before retrying
    raise RuntimeError("gateway unavailable")
'''
CODE = {"v0": V0, "v1": V1, "v2": V2, "v3": V3}
TB = ["Traceback (most recent call last):", '  File "app.py", line 8, in <module>', "    charge(order)",
      '  File "api_client.py", line 8, in charge']
S = {}

# ------------------------------------------------------------------ run the real scenario
(repo / "api_client.py").write_text(V0, encoding="utf-8")
git("init", "-q", "-b", "main")
git("add", "-A")
git("commit", "-qm", "Add payment client")
rw("capture")
S["s0"] = state()

git("checkout", "-q", "-b", "feature/payment-timeout")
(repo / "api_client.py").write_text(V1, encoding="utf-8")
rw("capture")
S["s1"] = state()

out = rw("record", "--command", "python app.py", "--exit", "1", "--id", "r1",
         stdin="\n".join(TB) + "\nTimeoutError: gateway timed out after 30s\n")
REC1 = notes(out)
S["s2"] = state()

(repo / "api_client.py").write_text(V2, encoding="utf-8")
rw("capture")
out = rw("record", "--command", "python app.py", "--exit", "1", "--id", "r2",
         stdin="\n".join(TB) + "\nTimeoutError: gateway timed out after 60s\n")
REC2 = notes(out)
S["s3"] = state()

(repo / "api_client.py").write_text(V3, encoding="utf-8")
rw("capture")
git("commit", "-qam", "Use retry with backoff", "-m",
    "Raising the timeout did not help because the gateway drops idle connections")
rw("capture")
out = rw("record", "--command", "python app.py", "--exit", "0", "--id", "r3", stdin="Charged order 1042: OK\n")
REC3 = notes(out)
S["s4"] = state()

rw("autopause")  # leaving for the day
(repo / "requirements.txt").write_text("requests==2.32.3\n", encoding="utf-8")
git("add", "-A", name="Sam Lee")
git("commit", "-qm", "Merge pull request #12 from sam/pricing-update", name="Sam Lee")
S["s5"] = state()
AWAY_N = len(S["s5"]["away"]["commits"]) if S["s5"].get("away") else 0

graph_file = WORK / "graph.html"
rw("graph", "-o", str(graph_file))
gdata = json.loads(rw("graph", "--format", "json"))
because = [e for e in gdata["edges"] if e["rel"] == "because"]
dec = next((n for n in gdata["nodes"] if n["type"] == "decision" and any(e["s"] == n["id"] for e in because)), None)
LINKED_AUTOMATICALLY = dec is not None
if dec is None:  # the engine did not link it on its own: record the reasoning explicitly so the demo shows the walk
    rw("decide", "Use retry with exponential backoff", "--why", "raising the timeout did not help; the gateway drops idle connections")
    rw("graph", "-o", str(graph_file))
    gdata = json.loads(rw("graph", "--format", "json"))
    because = [e for e in gdata["edges"] if e["rel"] == "because"]
    dec = next(n for n in gdata["nodes"] if n["type"] == "decision" and any(e["s"] == n["id"] for e in because))
HANDOFF = rw("handoff")
print("scenario ok | decision linked automatically:", LINKED_AUTOMATICALLY, "| decision node:", dec["id"], "| away commits:", AWAY_N, "| notes:", REC1, REC2, REC3)

# ------------------------------------------------------------------ rendering helpers
FR = WORK / "frames"
rmtree(FR)
FR.mkdir()
PANEL_SRC = (ROOT / "vscode-extension" / "panel.html").read_text(encoding="utf-8").replace("__NONCE__", "x")
PANEL_SRC = re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]*>', "", PANEL_SRC)
THEME = """<style>:root{--vscode-foreground:#cccccc;--vscode-editor-background:#1f1f1f;--vscode-editorWidget-background:#202020;
--vscode-widget-border:#2b2b2b;--vscode-descriptionForeground:#9d9d9d;--vscode-button-background:#0078d4;--vscode-button-foreground:#fff;
--vscode-button-hoverBackground:#026ec1;--vscode-font-family:'Segoe UI',sans-serif;--vscode-editor-font-family:Consolas,monospace;
--vscode-toolbar-hoverBackground:#ffffff14}html{background:#181818}body{margin:0}</style>"""
_panel_cache = {}


def panel_page(skey, opens, shift_h, ev_open, scroll, open_ev=(), scroll_to=""):
    key = (skey, json.dumps(opens, sort_keys=True), shift_h, ev_open, scroll, tuple(open_ev), scroll_to)
    if key not in _panel_cache:
        stub = ("<script>const _n=Date.now;Date.now=()=>_n()+%d;window.acquireVsCodeApi=()=>({postMessage(m){if(m.cmd==='ready')"
                "setTimeout(()=>window.postMessage({state:%s},'*'),20)},getState:()=>({open:%s}),setState(){}});"
                "setTimeout(()=>{const want=%s;document.querySelectorAll('details.ev').forEach((d,i)=>{const h=d.closest('.row,.card');"
                "if(i<%d||want.some(w=>h&&h.innerText.includes(w)))d.open=true});const st=%s;"
                "if(st){const el=[...document.querySelectorAll('summary')].find(e=>e.innerText.includes(st));if(el)el.scrollIntoView({block:'start'})}"
                "else window.scrollTo(0,%d)},250);</script>"
                % (int(shift_h * 3600 * 1000), json.dumps(S[skey]), json.dumps(opens), json.dumps(list(open_ev)), ev_open,
                   json.dumps(scroll_to), scroll))
        p = FR / f"panel_{len(_panel_cache)}.html"
        p.write_text(PANEL_SRC.replace("</head>", THEME + stub + "</head>"), encoding="utf-8")
        _panel_cache[key] = p
    return _panel_cache[key]


def url(p):
    return "file:///" + str(p).replace("\\", "/")


KW = {"import", "def", "for", "in", "try", "except", "return", "raise", "from", "if", "else", "range"}


def hl(line):
    out = []
    for m in re.finditer(r'(#.*$)|("[^"]*"|\'[^\']*\')|(\b\d+\b)|([A-Za-z_]\w*)|(\s+|.)', line):
        c, s, d, w, o = m.groups()
        t = html.escape(m.group(0))
        if c:
            out.append(f'<span class="c">{t}</span>')
        elif s:
            out.append(f'<span class="s">{t}</span>')
        elif d:
            out.append(f'<span class="d">{t}</span>')
        elif w and w in KW:
            out.append(f'<span class="k">{t}</span>')
        elif w and re.match(r"^\s*def\s", line[:m.start()] + "def ") and False:
            out.append(t)
        else:
            out.append(t)
    return "".join(out)


CSS = """*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#1f1f1f;font-family:'Segoe UI',system-ui,sans-serif;color:#ccc}
.win{width:1280px;height:720px;display:flex;flex-direction:column}
.title{height:30px;background:#181818;border-bottom:1px solid #2b2b2b;display:flex;align-items:center;justify-content:center;font-size:12px;color:#9d9d9d;position:relative}
.dots{position:absolute;left:12px;display:flex;gap:7px}.dots i{width:11px;height:11px;border-radius:50%;background:#444}
.body{flex:1;display:flex;min-height:0;position:relative}
.act{width:48px;background:#181818;border-right:1px solid #2b2b2b;display:flex;flex-direction:column;align-items:center;padding-top:10px;gap:10px}
.act b{width:26px;height:26px;border-radius:7px;background:#333;opacity:.6}.act b.on{background:#0078d4;opacity:1}
.side{width:322px;background:#181818;border-right:1px solid #2b2b2b;display:flex;flex-direction:column}
.side h5{margin:0;padding:10px 14px 2px;font-size:11px;letter-spacing:.08em;color:#9d9d9d;font-weight:500}
.side iframe{border:0;flex:1;width:100%}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.tabs{height:35px;background:#181818;border-bottom:1px solid #2b2b2b;display:flex}
.tab{padding:0 16px;display:flex;align-items:center;font-size:13px;background:#1f1f1f;border-right:1px solid #2b2b2b;border-top:1px solid #0078d4}
.code{flex:1;padding:62px 0 0;font:15px/24px Consolas,'Courier New',monospace;overflow:hidden}
.ln{display:flex;white-space:pre}.ln .n{width:56px;text-align:right;padding-right:22px;color:#6e7681;flex:none}.ln.cur{background:#ffffff10}
.k{color:#569cd6}.s{color:#ce9178}.c{color:#6a9955}.d{color:#b5cea8}
.term{height:200px;background:#181818;border-top:1px solid #2b2b2b;display:flex;flex-direction:column}
.term h6{margin:0;padding:6px 14px;font-size:11px;letter-spacing:.08em;color:#9d9d9d;font-weight:500;border-bottom:1px solid #222}
.tl{padding:6px 14px;font:13px/19px Consolas,monospace;overflow:hidden;flex:1;white-space:pre}.err{color:#f48771}.ok{color:#89d185}.pr{color:#cccccc}.cmd{color:#e5c07b}
.cap{position:absolute;top:44px;left:825px;transform:translateX(-50%);background:#0b0b0bdd;color:#fff;padding:9px 22px;border-radius:12px;font-size:21px;font-weight:600;max-width:880px;text-align:center;z-index:10;border:1px solid #ffffff2a;box-shadow:0 8px 24px #000a;white-space:nowrap}
.toast{position:absolute;right:16px;bottom:214px;width:430px;background:#252526;border:1px solid #454545;border-radius:6px;padding:12px 14px;font-size:13px;line-height:1.5;box-shadow:0 6px 22px #000b;z-index:9}
.toast .t{display:flex;gap:10px}.toast .ic{width:16px;height:16px;border-radius:50%;flex:none;margin-top:3px}
.toast button{margin-top:8px;background:#0078d4;color:#fff;border:0;border-radius:3px;padding:4px 12px;font-size:12px}
.card{width:1280px;height:720px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;background:radial-gradient(ellipse at 30% 20%,#12304f,#0e0f14 70%)}
.card h1{font-size:64px;margin:0 0 14px;color:#fff;letter-spacing:-1px}.card p{font-size:26px;margin:6px 0;color:#c8d1dc;max-width:980px;line-height:1.4}
.card .pill{margin-top:26px;padding:12px 28px;border-radius:999px;background:#0078d4;color:#fff;font-size:24px;font-weight:600}
.capbar{height:52px;background:#0b0b0b;display:flex;align-items:center;justify-content:center;font-size:21px;font-weight:600;color:#fff;border-top:1px solid #2b2b2b}
.chat{flex:1;display:flex;flex-direction:column;align-items:center;padding:62px 70px 30px;background:#161719}
.chat .box{width:1060px;flex:1;background:#202225;border:1px solid #3a3d41;border-radius:14px;padding:18px 22px;font:13.5px/19px Consolas,monospace;color:#d4d4d4;overflow:hidden;white-space:pre-wrap}
.chat .lbl{width:1060px;font-size:15px;color:#9aa0a6;margin-bottom:10px}
"""


def shell(body_html, caption, title="checkout-service - Visual Studio Code"):
    cap = f'<div class="cap">{html.escape(caption)}</div>' if caption else ""
    return (f"<!doctype html><meta charset=utf-8><style>{CSS}</style><div class=win><div class=title><div class=dots><i></i><i></i><i></i></div>"
            f"{html.escape(title)}</div><div class=body>{body_html}{cap}</div></div>")


def ide(kf):
    code = CODE[kf["code"]].rstrip("\n").split("\n")
    cur = kf.get("cur")
    lines = "".join(f'<div class="ln {"cur" if i + 1 == cur else ""}"><span class=n>{i + 1}</span><span>{hl(l)}</span></div>' for i, l in enumerate(code))
    term = "".join(f'<div class="{c}">{html.escape(t) or "&nbsp;"}</div>' for t, c in kf.get("term", []))
    toast = ""
    if kf.get("toast"):
        icon, texts = kf["toast"]
        col = {"warn": "#cca700", "info": "#3794ff", "ok": "#89d185"}[icon]
        toast = (f'<div class=toast><div class=t><span class=ic style="background:{col}"></span><div>'
                 + "<br>".join(html.escape(t) for t in texts) + '</div></div><button>Open Rewind</button></div>')
    pp = panel_page(kf["state"], kf.get("open", {}), kf.get("shift", 0), kf.get("ev", 1), kf.get("scroll", 0),
                    kf.get("open_ev", ()), kf.get("scroll_to", ""))
    body = (f'<div class=act><b></b><b></b><b></b><b></b><b class=on></b></div><div class=side><h5>INSTANT REWIND</h5><iframe src="{url(pp)}"></iframe></div>'
            f'<div class=main><div class=tabs><div class=tab>api_client.py</div></div><div class=code>{lines}</div>'
            f'<div class=term><h6>TERMINAL</h6><div class=tl>{term}</div></div></div>{toast}')
    return shell(body, kf["cap"])


def graph(kf):
    sel = f"#select={dec['id']}" if kf.get("select") else ""
    body = (f'<div style="display:flex;flex-direction:column;width:1280px"><iframe src="{url(graph_file)}{sel}" '
            f'style="border:0;width:1280px;height:638px"></iframe><div class=capbar>{html.escape(kf["cap"])}</div></div>')
    return shell(body, None, "Rewind: Context Graph - checkout-service")


def chat(kf):
    text = "\n".join(HANDOFF.splitlines()[:21])
    body = (f'<div class=chat><div class=lbl>Message to any AI assistant (pasted from Rewind: Copy for AI)</div>'
            f'<div class=box>{html.escape(text)}</div></div>')
    return shell(body, kf["cap"], "Any AI chat")


def card(kf):
    return f"<!doctype html><meta charset=utf-8><style>{CSS}</style><div class=card>{kf['html']}</div>"


# ------------------------------------------------------------------ the storyboard
def typed(cmd, upto):
    return ("PS C:\\checkout-service> ", "pr"), (cmd[:upto], "cmd")


def term_cmd(cmd, upto=None, output=()):
    p, c = typed(cmd, len(cmd) if upto is None else upto)
    return [(p[0] + c[0], "cmd")] + list(output)


ERR1 = [(t, "err") for t in TB] + [("TimeoutError: gateway timed out after 30s", "err")]
ERR2 = [(t, "err") for t in TB] + [("TimeoutError: gateway timed out after 60s", "err")]
OKOUT = [("Charged order 1042: OK", "ok")]
OPEN = {"tasks": True, "dec": True, "dead": True, "time": True}
KF = []


def add(dur, kind="ide", **kw):
    KF.append({"dur": dur, "kind": kind, **kw})


add(3.2, "card", html="<h1>Project Rewind</h1><p>Never lose your place again.</p><p>Memory for developers and AI coding agents.</p>")
c1 = "1. Open a project. Rewind starts watching. Nothing to set up."
add(2.8, cap=c1, code="v0", state="s0", cur=7)
c2 = "2. Edit on a feature branch. Rewind infers the task, and shows why."
add(4.8, cap=c2, code="v1", state="s1", cur=6, ev=1, open={"tasks": True})
c3 = "3. Run your app. The error is captured automatically."
cmd = "python app.py"
add(0.6, cap=c3, code="v1", state="s1", term=term_cmd(cmd, 7), open={"tasks": True}, ev=0)
add(0.7, cap=c3, code="v1", state="s1", term=term_cmd(cmd), open={"tasks": True}, ev=0)
add(1.2, cap=c3, code="v1", state="s1", term=term_cmd(cmd, output=ERR1), open={"tasks": True}, ev=0)
add(4.6, cap=c3, code="v1", state="s2", term=term_cmd(cmd, output=ERR1), toast=("warn", REC1[:2] or ["new error logged"]), ev=0)
c4 = "4. Try a fix. The same error returns. Rewind remembers what failed."
add(1.4, cap=c4, code="v2", state="s2", cur=8, term=term_cmd(cmd, output=ERR1))
add(1.2, cap=c4, code="v2", state="s2", term=term_cmd(cmd, output=ERR2), cur=8)
add(6.4, cap=c4, code="v2", state="s3", term=term_cmd(cmd, output=ERR2), toast=("warn", REC2[:3]), open=OPEN, ev=0,
    open_ev=["Edited api_client"], scroll_to="Dead ends")
c5 = "5. Fix it for real. The fix and the reasons are recorded too."
add(1.6, cap=c5, code="v3", state="s3", cur=11, term=term_cmd(cmd, output=ERR2), open=OPEN, ev=0, scroll_to="Dead ends")
add(6.4, cap=c5, code="v3", state="s4", term=term_cmd(cmd, output=OKOUT), toast=("ok", REC3[:2]), open=OPEN, ev=0,
    open_ev=["Use retry with backoff"], scroll_to="Decisions")
c6 = "6. Come back tomorrow. Rewind shows what changed while you were away."
away_msg = [f"Welcome back. {AWAY_N} new commit(s) while you were away."]
add(7.5, cap=c6, code="v3", state="s5", shift=14, toast=("info", away_msg), open={"tasks": True}, ev=0, term=term_cmd("", 0))
add(3.4, "graph", cap="7. The context graph: milestones on a timeline, every memory linked.")
add(7.0, "graph", cap="8. Click any decision to walk back to its cause, step by step.", select=True)
add(7.0, "chat", cap="9. One click copies a handoff. Paste it into any AI chat. It already knows.")
add(4.6, "card", html="<h1>Project Rewind</h1><p>Local-first. Open source. MIT license.</p><p>VS Code extension, MCP server, context graph.</p><div class=pill>github.com/Dritikakotian/project-rewind</div>")

# ------------------------------------------------------------------ render + encode
jobs = []
for i, kf in enumerate(KF):
    page = {"ide": ide, "graph": graph, "chat": chat, "card": card}[kf["kind"]](kf)
    hp = FR / f"f{i:03d}.html"
    hp.write_text(page, encoding="utf-8")
    jobs.append((hp, FR / f"f{i:03d}.png", i))


def shoot(job):
    hp, png, i = job
    prof = FR / f"prof{i}"
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={prof}",
                    "--window-size=1280,720", "--force-device-scale-factor=1", "--virtual-time-budget=4500",
                    f"--screenshot={png}", url(hp) + (f"#select={dec['id']}" if False else "")],
                   capture_output=True, timeout=120)
    shutil.rmtree(prof, ignore_errors=True)
    return png.exists()


with ThreadPoolExecutor(4) as ex:
    ok = list(ex.map(shoot, jobs))
print("frames:", sum(ok), "/", len(jobs))
if not all(ok):
    sys.exit("some frames failed")

OUT.mkdir(exist_ok=True)
lst = FR / "list.txt"
lst.write_text("".join(f"file '{p.name}'\nduration {kf['dur']}\n" for (h, p, i), kf in zip(jobs, KF)) +
               f"file '{jobs[-1][1].name}'\n", encoding="utf-8")
mp4 = OUT / "project-rewind-demo.mp4"
subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
                "fps=12,format=yuv420p", "-c:v", "libx264", "-crf", "24", "-preset", "medium", "-movflags", "+faststart", str(mp4)],
               check=True)
print("video:", mp4, round(mp4.stat().st_size / 1024), "KB,", round(sum(k["dur"] for k in KF), 1), "s")
