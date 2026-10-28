"""CLI command handlers."""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from .util import *  # noqa: F401,F403
from .storage import *  # noqa: F401,F403
from .project import *  # noqa: F401,F403
from .semantic import *  # noqa: F401,F403
from .reasoning import *  # noqa: F401,F403
from .memory import *  # noqa: F401,F403
from .handoff import *  # noqa: F401,F403
from .graph import *  # noqa: F401,F403
from .simplify import *  # noqa: F401,F403
from .simplify import _depth  # noqa: F401


# ---------------------------------------------------------------- commands
def print_brief(p, at=None, away=None):
    cutoff = at or "9999"
    pauses = p.memories("pause", at)
    cps = p.db.execute("SELECT * FROM checkpoints WHERE ts<=? ORDER BY id DESC LIMIT 6", (cutoff,)).fetchall()
    tasks = p.tasks(at)
    cur = next((t for t in reversed(tasks) if not t[2]), None)
    evs = p.db.execute("SELECT * FROM events WHERE ts<=? ORDER BY id DESC LIMIT 6", (cutoff,)).fetchall()
    out(f"== {p.root.name} " + (f"(as of {at})" if at else f"(branch {p.branch() or '-'})") + " ==")
    out(f"Working on : {cur[1] if cur else '(no task set - use `rewind task \"...\"`)'}")
    done = [t for t in tasks if t[2]]
    out(f"Done       : {', '.join(t[1][:60] for t in done[-4:]) or '-'}")
    out(f"Remaining  : {', '.join(t[1][:60] for t in tasks if not t[2]) or '-'}")
    short = lambda v: (v or "-")[:100] + ("..." if v and len(v) > 100 else "")  # noqa: E731
    labels = (("hypothesis", "hypothesis "), ("testing", "testing    "), ("result", "last result"), ("next", "next       "))
    newer = p.activity_since_pause()
    st = suggest_state(p) if not at and newer else None
    if st:  # nothing saved since the last activity: show the inferred state instead of an outdated pause
        out("Where you probably stopped (auto-detected):")
        for k, label in labels:
            out(f"  {label}: {short(st[k])}")
    elif pauses:
        d = p.nd(pauses[-1])
        out(f"Paused {ago(pauses[-1]['ts'])}{' (auto)' if d.get('auto') else ''}:")
        for k, label in labels:
            out(f"  {label}: {short(d.get(k))}")
        stale = p.stale_note(pauses[-1])
        if stale and not at:
            out(stale)
    decs = p.memories("decision", at)[-3:]
    if decs:
        out("Why (recent decisions):")
        for r in decs:
            out(f"  - {r['label']}" + (f" -- {p.nd(r).get('why')}" if p.nd(r).get("why") else ""))
    if not at:
        if p.is_git:
            dirty = p.dirty()
            out(f"Uncommitted: {', '.join(x for _, x in dirty[:6]) or 'clean'}")
        else:
            out(f"Recently edited: {', '.join(p.hot_files(6)) or '-'}  (not a git repo: git features off)")
    out("Last activity:")
    for e in evs:
        out(f"  {ago(e['ts']):>9}  {e['kind']:<7} {e['summary'][:90]}")
    out("Recent milestones:")
    for c in cps:
        out(f"  #{c['id']:<3} {ago(c['ts']):>9}  {c['kind']:<8} {c['title'][:80]}")
    fails = p.memories("failed", at)
    if fails:
        out(f"Failed paths on record: {len(fails)} (see `rewind failed`)")
    if away is not None:
        print_away(away)


def print_away(a):
    if a is None:
        return
    if not a["commits"]:
        out("While you were away: no new commits.")
        return
    out("While you were away:")
    for h, au, s, mine in a["commits"][:8]:
        out(f"  {h} {'(you)' if mine else '(' + au + ')'} {s[:80]}")
    if a["merges"]:
        out(f"  merged: {'; '.join(a['merges'][:3])}")
    if a["deps"]:
        out(f"  dependency files changed: {', '.join(a['deps'])}")
    out(f"  {len(a['files'])} file(s) changed in total")


def cmd_show(p, a):
    away = p.away()
    print_brief(p, away=away)
    p.snapshot()


def cmd_capture(p, a):
    notes = p.snapshot(a.file)
    if notes:
        export_context(p)
    out("captured: " + (", ".join(notes) if notes else "nothing new"))


def cmd_watch(p, a):
    out(f"watching {p.root} every {a.interval}s (Ctrl+C to stop)")
    last = None
    try:
        while True:
            sig = p.git("status", "--porcelain") + p.head()
            if sig != last:
                notes = p.snapshot()
                if notes:
                    out(time.strftime("%H:%M:%S ") + ", ".join(notes))
                last = sig
            time.sleep(a.interval)
    except KeyboardInterrupt:
        out("stopped")


def cmd_task(p, a):
    if a.done:
        tid = p.get("task_id")
        if not tid:
            out("no current task")
            return 1
        p.update_data(int(tid), status="done", done_ts=now())
        row = p.db.execute("SELECT label FROM nodes WHERE id=?", (tid,)).fetchone()
        p.checkpoint("task-done", f"Completed: {row['label']}")
        p.put("task_id", None)
        out(f"completed: {row['label']}")
    elif a.text:
        tid = p.node("task", a.text, data={"status": "open"})
        p.put("task_id", tid)
        p.event("task", f"task: {a.text}")
        cid = p.checkpoint("start", f"Started: {a.text}", task=tid)
        out(f"task set (checkpoint #{cid})")
    else:
        for tid, label, done in p.tasks():
            out(f"[{'x' if done else ' '}] {label}")


def cmd_checkpoint(p, a):
    out(f"checkpoint #{p.checkpoint('manual', a.title)}")


def cmd_pause(p, a):
    vals = {"hypothesis": a.hypothesis, "testing": a.testing, "result": a.result, "next": a.next}
    if sys.stdin.isatty():
        for k in vals:
            if vals[k] is None:
                vals[k] = input(f"{k} (Enter = auto)? ") or None
    guess = suggest_state(p) or {}
    vals = {k: (v if v else guess.get(k)) for k, v in vals.items()}  # anything left blank is inferred
    cid = save_pause(p, vals)
    out(f"state saved at checkpoint #{cid}. Resume with `rewind`.")


def cmd_autopause(p, a):
    """Save an inferred pause if anything happened since the last one (called on idle / editor close)."""
    p.snapshot()
    last = p.memories("pause")
    if last and not p.activity_since_pause():
        out("nothing new since last pause")
        return
    guess = suggest_state(p)
    if not guess:
        out("no activity to save")
        return
    out(f"auto-saved at checkpoint #{save_pause(p, guess, auto=True)}")


def cmd_suggest(p, a):
    out(json.dumps(suggest_state(p) or {}))


def cmd_decide(p, a):
    n = add_memory(p, "decision", a.text, a.file, why=a.why)
    link_reasoning(p, n, a.text + " " + (a.why or ""))
    if p.get("task_id"):
        p.link(int(p.get("task_id")), n, "decided")
    p.event("decision", a.text)
    out("decision recorded")


def cmd_fail(p, a):
    cid = p.checkpoint("attempt", f"Failed attempt: {a.text}")
    n = add_memory(p, "failed", a.text, a.file, why=a.why, cp=cid)
    err = p.db.execute("SELECT id FROM nodes WHERE type='error' AND json_extract(data,'$.status')='open' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    if err:
        p.link(n, err["id"], "attempt-for")
    p.event("failed", a.text)
    out(f"failed path recorded at checkpoint #{cid}")


def cmd_solved(p, a):
    err = p.db.execute("SELECT id,label FROM nodes WHERE type='error' AND json_extract(data,'$.status')='open' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    if not err:
        out("no open error")
        return 1
    p.update_data(err["id"], status="solved", solution=a.text, solved_ts=now())
    resolve_error(p, err["id"], p.checkpoint("fix", f"Fixed: {err['label'][:60]} via {a.text}"))
    out("recorded solution")


def cmd_run(p, a):
    cmd = a.command[1:] if a.command and a.command[0] == "--" else a.command
    if not cmd:
        out("usage: rewind run -- <command>")
        return 2
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        r = subprocess.run(" ".join(cmd), shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    record_cmd(p, " ".join(cmd), r.returncode, r.stderr + "\n" + r.stdout)
    return r.returncode


def cmd_record(p, a):
    """Called by editor/terminal integrations after a command finishes; output arrives on stdin."""
    p.snapshot()
    text = a.command.strip()
    if not text or re.match(r"^(python\S*\s+\S*rewind\.py|rewind)\b", text):
        return 0
    output = sys.stdin.read()[-20000:] if not sys.stdin.isatty() else ""
    record_cmd(p, text[:300], a.exit, output, exec_id=a.id)


def cmd_check(p, a):
    want = tokens(a.text)
    hits = []
    for r in p.memories("failed"):
        have = tokens(r["label"])
        shared = want & have
        score = similarity(a.text, r["label"] + " " + (p.nd(r).get("why") or ""))
        if score >= 0.45 or len(shared) >= 2 or (have and len(shared) / len(have) >= 0.6):
            hits.append(r)
    if not hits:
        out("no matching failed paths")
        return 0
    for r in hits:
        d = p.nd(r)
        out(f"WARNING: already attempted at checkpoint #{d.get('cp', '?')} ({ago(r['ts'])}) and failed: "
            f"{r['label']} -- because: {d.get('why') or 'not recorded'}")
    return 3


def cmd_failed(p, a):
    for r in p.memories("failed"):
        out(f"#{p.nd(r).get('cp', '?')} {r['label']} -- {p.nd(r).get('why') or '-'}{p.stale_note(r)}")
    for r in p.memories("error"):
        d = p.nd(r)
        out(f"error x{d.get('count', 1)} [{d.get('status')}] {r['label']}" + (f" => {d['solution']}" if d.get("solution") else ""))


def why_file(p, f):
    f = f.replace("\\", "/")
    node = p.db.execute("SELECT id FROM nodes WHERE type='file' AND key=?", (f,)).fetchone()
    out(f"Why {f}?")
    if node:
        for r in p.db.execute("SELECT n.* FROM nodes n JOIN edges e ON e.src=n.id WHERE e.dst=? AND n.type IN "
                              "('decision','failed','error','pause') ORDER BY n.id", (node["id"],)):
            d = p.nd(r)
            out(f"  [{r['type']}] {r['label']}" + (f" -- {d.get('why')}" if d.get("why") else "") + p.stale_note(r))
        for r in p.db.execute("SELECT n.* FROM nodes n JOIN edges e ON e.src=n.id WHERE e.dst=? AND n.type='commit' "
                              "ORDER BY n.id DESC LIMIT 5", (node["id"],)):
            out(f"  [commit] {r['label']}")
    else:
        out("  no recorded history")


def cmd_why(p, a):
    why_file(p, a.file)


def cmd_ignore(p, a):
    with open(p.root / ".rewindignore", "a", encoding="utf-8") as fh:
        fh.write(a.pattern + "\n")
    out(f"ignoring {a.pattern}")


def cmd_at(p, a):
    cp = p.db.execute("SELECT * FROM checkpoints WHERE id=?", (a.checkpoint,)).fetchone()
    if not cp:
        out("no such checkpoint")
        return 1
    out(f"What I knew at checkpoint #{cp['id']} [{cp['kind']}] {cp['title']}")
    print_brief(p, at=cp["ts"])


def cmd_checkpoints(p, a):
    for c in p.db.execute("SELECT * FROM checkpoints ORDER BY id DESC LIMIT ?", (a.n,)):
        out(f"#{c['id']:<3} {c['ts']} {c['kind']:<9} {c['branch'] or '-':<14} {c['title'][:70]}")


def resume_preview(p, cp):
    """What Resume would do, before it does anything."""
    patch = unseal(cp["patch"] or "")
    files = re.findall(r"^diff --git a/(.+?) b/", patch, re.M)
    clean = None
    if patch and p.is_git:
        r = subprocess.run(["git", "-C", str(p.root), "apply", "--check"], input=patch.encode("latin-1"),
                           capture_output=True)
        clean = r.returncode == 0
    snap = json.loads(unseal(cp["untracked"] or "") or "{}")
    exists = {rel: (p.root / rel).exists() for rel in snap}
    differ = [rel for rel in snap if exists[rel] and (p.root / rel).read_bytes().decode("utf-8", "replace") != snap[rel]]
    same = sum(1 for rel in snap if exists[rel]) - len(differ)
    out(f"  tracked files in the saved patch : {len(files)}" +
        ("" if clean is None else f"  (applies cleanly to current files: {'yes' if clean else 'NO, conflicts likely'})"))
    out(f"  files that would be recreated    : {sum(1 for v in exists.values() if not v)}")
    out(f"  saved as side copies (they differ): {len(differ)}")
    out(f"  already identical (untouched)    : {same}")
    if p.is_git:
        out(f"  your current changes ({len(p.dirty())} file(s)) are stashed first, never discarded")


def cmd_resume(p, a):
    cp = p.db.execute("SELECT * FROM checkpoints WHERE id=?", (a.checkpoint,)).fetchone()
    if not cp:
        out("no such checkpoint")
        return 1
    out(f"Plan to resume from #{cp['id']} {cp['title']}")
    out(f"  branch {cp['branch']} @ {cp['head'][:8]}, {len(json.loads(cp['dirty']))} uncommitted file(s), "
        f"patch {'saved' if cp['patch'] else 'none'}")
    if not a.apply:
        resume_preview(p, cp)
        out("dry run - re-run with --apply to stash current work, switch, and restore")
        return 0
    if p.is_git:
        if p.dirty():
            out("  stash: " + (p.git("stash", "push", "-u", "-m", f"rewind-auto-{now()}").strip() or "failed"))
        on_branch = cp["branch"] and cp["branch"] != "HEAD" and p.git("rev-parse", "--verify", "-q", cp["branch"]).strip()
        if on_branch and p.git("rev-parse", cp["branch"]).strip() == cp["head"]:
            p.git("checkout", cp["branch"])
        else:
            p.git("checkout", "--detach", cp["head"])
            out(f"  detached at milestone commit (branch {cp['branch']} has moved on)")
        if cp["patch"]:
            r = subprocess.run(["git", "-C", str(p.root), "apply", "--3way"], input=unseal(cp["patch"]).encode("latin-1"),
                               capture_output=True)  # bytes in, bytes out: no newline translation on Windows
            out("  patch applied" if r.returncode == 0 else
                f"  patch failed: {r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    # untracked / non-git files: never overwrite; differing files get a sidecar copy
    for rel, text in json.loads(unseal(cp["untracked"] or "") or "{}").items():
        dest = p.root / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(text.encode("utf-8"))  # bytes: no newline translation on Windows
            out(f"  restored {rel}")
        elif dest.read_bytes().decode("utf-8", "replace") != text:
            side = dest.with_name(dest.name + f".rewind-cp{cp['id']}")
            side.write_bytes(text.encode("utf-8"))
            out(f"  {rel} differs; saved milestone version as {side.name}")
    if cp["task"]:
        p.put("task_id", cp["task"])
    p.snapshot()
    print_brief(p)


def cmd_handoff(p, a):
    at = None
    if a.at:
        cp = p.db.execute("SELECT ts FROM checkpoints WHERE id=?", (a.at,)).fetchone()
        if not cp:
            out("no such checkpoint")
            return 1
        at = cp["ts"]
    text = build_handoff(p, at, a.budget, a.target, a.focus)
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
        out(f"wrote {a.output} (~{len(text) // 4} tokens)")
    else:
        out(text)
        out(f"[~{len(text) // 4} tokens]")


# ---- ask
def cmd_ask(p, a):
    q = a.question
    ql = q.lower()
    at = None
    if a.at:
        cp = p.db.execute("SELECT ts FROM checkpoints WHERE id=?", (a.at,)).fetchone()
        at = cp["ts"] if cp else None
    if re.search(r"\bknow\b|point in time|at that time", ql) and at:
        print_brief(p, at=at)
    elif re.search(r"\bwhy\b", ql):
        known = {r["key"] for r in p.db.execute("SELECT key FROM nodes WHERE type='file'")}
        f = next((k for k in known if k in q.replace("\\", "/") or os.path.basename(k) in q), None)
        if f:
            why_file(p, f)
        else:
            for r in p.memories("decision", at):
                out(f"- {r['label']} -- {p.nd(r).get('why') or ''}")
    elif re.search(r"tried|attempt|fail|fix", ql):
        cmd_failed(p, a)
    elif re.search(r"\bnext\b|todo|should i|remain|left to do", ql):
        pauses = p.memories("pause", at)
        if pauses:
            out("Intended next: " + (p.nd(pauses[-1]).get("next") or "-"))
        for _, label, done in p.tasks(at):
            if not done:
                out(f"Open task: {label}")
        for r in p.memories("error", at):
            if p.nd(r).get("status") == "open":
                out(f"Unresolved error: {r['label']}")
    elif re.search(r"what (did|have) i (do|done|change|work)|changed|worked on|recent", ql):
        for r in p.db.execute("SELECT * FROM events WHERE ts<=? ORDER BY id DESC LIMIT 10", (at or "9999",)):
            out(f"{ago(r['ts']):>9}  {r['kind']:<7} {r['summary'][:100]}")
    elif re.search(r"where|stop|left off|last", ql):
        print_brief(p, at=at)
    else:
        scored = []
        for r in p.db.execute("SELECT * FROM nodes WHERE type!='file' AND ts<=? ORDER BY id DESC LIMIT 300", (at or "9999",)):
            d = p.nd(r)
            text = r["label"] + " " + " ".join(str(d.get(k) or "") for k in ("why", "solution", "hypothesis", "next"))
            sc = similarity(q, text)
            if sc >= 0.2:
                scored.append((sc + 0.05, f"[{r['type']}] {r['label']}"))
        for r in p.db.execute("SELECT * FROM events WHERE ts<=? ORDER BY id DESC LIMIT 200", (at or "9999",)):
            sc = similarity(q, r["summary"])
            if sc >= 0.2:
                scored.append((sc, f"[{ago(r['ts'])}] {r['summary'][:100]}"))
        for _, line in sorted(scored, key=lambda x: -x[0])[:6]:
            out(line)
        if not scored:
            out("nothing relevant recorded")


def cmd_simplify(p, a):
    """Heuristic over-engineering check (suggestions only; it never edits your code)."""
    f = Path(a.file) if os.path.isabs(a.file) else p.root / a.file
    if p.is_ignored(os.path.relpath(f, p.root)) or not f.is_file():
        out("file missing or excluded by privacy rules")
        return 1
    src = f.read_text(encoding="utf-8", errors="replace")
    try:
        tips = simplify_python(src) if f.suffix == ".py" else []
    except SyntaxError as e:
        out(f"cannot parse: {e}")
        tips = []
    tips += simplify_generic(src)
    if not tips:
        out("no simplification suggestions")
        return 0
    for line, what, fix in sorted(tips)[:25]:
        out(f"{f.name}:{line}  {what}  ->  {fix}")


def cmd_graph(p, a):
    g = build_graph(p, a.limit)
    if a.format == "json":
        text = json.dumps(g, indent=1)
    elif a.format == "mermaid":
        lines = ["graph LR"]
        clean = lambda t: t.replace('"', "'").replace("[", "(").replace("]", ")")[:50]  # noqa: E731
        lines += [f'  n{n["id"]}["{n["type"]}: {clean(n["label"])}"]' for n in g["nodes"]]
        lines += [f'  n{e["s"]} -->|{e["rel"]}| n{e["d"]}' for e in g["edges"]]
        text = "\n".join(lines)
    else:
        safe = json.dumps(g).replace("</", "<\\/")
        text = graph_template().replace("__DATA__", safe)
    target = a.output or (str(p.dir / "graph.html") if a.format == "html" else None)
    if target:
        Path(target).write_text(text, encoding="utf-8")
        out(target)
    else:
        out(text)


def cmd_state(p, a):
    """Structured project state for UIs (JSON). Also advances capture and records the away report."""
    at = None
    if a.at:
        cp = p.db.execute("SELECT ts FROM checkpoints WHERE id=?", (a.at,)).fetchone()
        at = cp["ts"] if cp else None
    if not at:
        rep = p.away()
        if rep and rep["commits"]:
            p.put("away_report", json.dumps({"ts": now(), "commits": rep["commits"][:8], "merges": rep["merges"][:3],
                                              "deps": rep["deps"], "files": len(rep["files"])}))
        p.snapshot()
        export_context(p)
    out(json.dumps(build_state(p, at)))


def cmd_dismiss_away(p, a):
    p.put("away_report", None)


def cmd_diag(p, a):
    """Editor problems (stdin JSON {file: [messages]}): new errors are remembered, vanished ones count as fixed."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 1
    present, made = set(), 0
    for rel, msgs in data.items():
        rel = rel.replace("\\", "/")
        if p.is_ignored(rel):
            continue
        for m in msgs[:5]:
            line = redact(m.strip().splitlines()[0] if m.strip() else "")[:160]
            if not line:
                continue
            sig = f"diag:{rel}:{re.sub(r'0x[0-9a-f]+|[0-9]+', 'N', line.lower())[:100]}"
            present.add(sig)
            row = p.db.execute("SELECT * FROM nodes WHERE type='error' AND key=?", (sig,)).fetchone()
            if row:
                if p.nd(row).get("status") != "open":
                    p.update_data(row["id"], status="open", last_ts=now(), count=p.nd(row).get("count", 1) + 1)
            else:
                n = p.node("error", f"{os.path.basename(rel)}: {line}", key=sig,
                           data={"count": 1, "status": "open", "diag": True, "file": rel, "last_ts": now(),
                                 "first_ts": now(), "hashes": p.hashes([rel])})
                p.link(n, p.file_node(rel), "about")
                if made < 3:  # a burst of new problems is one milestone, not dozens
                    p.checkpoint("bug", f"Error in {os.path.basename(rel)}: {line[:70]}")
                    made += 1
    for row in p.db.execute("SELECT * FROM nodes WHERE type='error' AND json_extract(data,'$.diag')=1 "
                            "AND json_extract(data,'$.status')='open'").fetchall():
        if row["key"] in present:
            continue
        d = p.nd(row)
        edited = p.edited_since(d.get("last_ts") or row["ts"])
        p.update_data(row["id"], status="solved", solved_ts=now(),
                      solution=("edited " + ", ".join(edited[:4])) if edited else "problem cleared")
        opened = d.get("first_ts") or row["ts"]
        if opened < time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 20)):
            resolve_error(p, row["id"], p.checkpoint("fix", f"Fixed: {row['label'][:70]}"))
    export_context(p)
    return 0


def cmd_projects(p, a):
    home = Path(os.environ.get("REWIND_HOME", Path.home() / ".rewind")) / "projects"
    for d in sorted(home.glob("*/rewind.db")):
        c = sqlite3.connect(d)
        try:
            kv = dict(c.execute("SELECT k,v FROM state").fetchall())
            task = c.execute("SELECT label FROM nodes WHERE id=?", (kv.get("task_id"),)).fetchone()
        except sqlite3.Error:
            continue
        finally:
            c.close()
        out(f"{d.parent.name:<32} active {ago(kv.get('last_active')):<10} task: {task[0] if task else '-'}")


def cmd_hook(p, a):
    hooks = p.root / ".git" / "hooks"
    if not hooks.exists():
        out("not a git repo")
        return 1
    line = f'"{sys.executable}" "{Path(__file__).resolve()}" --project "{p.root}" capture >/dev/null 2>&1 # rewind\n'
    for name in ("post-commit", "post-merge", "post-checkout"):
        f = hooks / name
        body = f.read_text() if f.exists() else "#!/bin/sh\n"
        if "# rewind" not in body:
            f.write_text(body + line)
    out("git hooks installed (post-commit, post-merge, post-checkout)")


def cmd_trace(p, a):
    out(trace(p, a.query, a.depth))


def cmd_gc(p, a):
    stats = gc(p.db, keep_patches=a.keep_patches, event_days=a.days)
    p.put("last_gc", now())
    out("cleaned: " + ", ".join(f"{k}={v}" for k, v in stats.items()))


AGENT_FILES = {"claude": "CLAUDE.md", "copilot": ".github/copilot-instructions.md", "agents": "AGENTS.md", "cursor": ".cursorrules"}


def cmd_agent_setup(p, a):
    """Print (or with --write, append) instructions that make AI coding agents consult Rewind first."""
    block = f"<!-- rewind:start -->\n## Project memory (Rewind)\n{AGENT_RULES}\n<!-- rewind:end -->\n"
    if not a.write:
        out(block)
        out(f"(add it to a file with: rewind agent-setup --target {a.target} --write -> {AGENT_FILES[a.target]})")
        return
    f = p.root / AGENT_FILES[a.target]
    f.parent.mkdir(parents=True, exist_ok=True)
    body = f.read_text(encoding="utf-8") if f.exists() else ""
    if "<!-- rewind:start -->" in body:
        out(f"{f.name} already contains the Rewind instructions")
        return
    f.write_text(body + ("\n" if body and not body.endswith("\n") else "") + block, encoding="utf-8")
    out(f"added Rewind instructions to {f}")


def cmd_confirm(p, a):
    """You checked an inferred memory and it is right: confidence becomes 100%."""
    if not p.db.execute("SELECT 1 FROM nodes WHERE id=?", (a.id,)).fetchone():
        out("no such memory")
        return 1
    p.update_data(a.id, confidence=1.0, confirmed=True)
    out("confirmed")


def cmd_dismiss(p, a):
    """You checked an inferred memory and it is wrong: hide it everywhere and never re-infer it."""
    if not p.db.execute("SELECT 1 FROM nodes WHERE id=?", (a.id,)).fetchone():
        out("no such memory")
        return 1
    p.update_data(a.id, dismissed=True)
    if p.get("task_id") == str(a.id):
        p.put("task_id", None)
    out("dismissed")
