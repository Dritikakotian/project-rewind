"""Inferred and recorded memories (pauses, decisions, failures, commands)."""
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


def suggest_state(p):
    """Infer hypothesis / testing / result / next from recent activity so nothing has to be typed."""
    err = p.db.execute("SELECT * FROM nodes WHERE type='error' AND json_extract(data,'$.status')='open' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    tasks = [t for t in p.tasks() if not t[2]]
    task = tasks[-1][1] if tasks else None
    hot = p.hot_files(3)
    cmd = p.db.execute("SELECT summary FROM events WHERE kind='cmd' ORDER BY id DESC LIMIT 1").fetchone()
    if not (err or hot or cmd or task):
        return None
    ed = p.nd(err) if err else {}
    last = cmd["summary"] if cmd else ""
    m = re.match(r"`(.*)` -> exit (\d+)", last)
    s_ = {
        "hypothesis": (f"{err['label'][:90]} (likely in {', '.join(hot[:2]) or 'recent edits'})" if err
                       else (f"Making progress on: {task}" if task else f"Changes in {', '.join(hot[:2])}")),
        "testing": (m.group(1) if m else (f"edits to {hot[0]}" if hot else "-")),
        "result": ((("failed" if m.group(2) != "0" else "passed") + f" (exit {m.group(2)})") if m else "no commands run yet")
                  + (f": {err['label'][:80]}" if err and m and m.group(2) != "0" else ""),
        "next": (f"fix: {err['label'][:80]}" + (f"; then re-run `{ed['cmd']}`" if ed.get("cmd") else "") if err
                 else (task or "-")),
    }
    return s_


def state_evidence(p):
    """The facts an inferred pause is based on, so the user can judge it."""
    ev = []
    err = p.db.execute("SELECT label FROM nodes WHERE type='error' AND json_extract(data,'$.status')='open' "
                       "AND json_extract(data,'$.dismissed') IS NOT 1 ORDER BY id DESC LIMIT 1").fetchone()
    cmd = p.db.execute("SELECT summary FROM events WHERE kind='cmd' ORDER BY id DESC LIMIT 1").fetchone()
    hot = p.hot_files(3)
    if err:
        ev.append(f"open error: {err['label'][:80]}")
    if cmd:
        ev.append(f"last command: {cmd['summary'][:100]}")
    if hot:
        ev.append(f"most recently edited: {', '.join(hot)}")
    return ev


def save_pause(p, vals, auto=False):
    p.snapshot()
    eid = p.last_event_id()
    evidence = state_evidence(p) if auto else ["entered by you"]
    files = p.hot_files()
    label = ("Auto-paused: " if auto else "Paused: ") + (vals.get("hypothesis") or vals.get("next") or "work interrupted")
    cid = p.checkpoint("pause", label[:120])
    n = p.node("pause", vals.get("hypothesis") or "paused",
               data={**vals, "hashes": p.hashes(files), "cp": cid, "auto": auto, "eid": eid, "evidence": evidence,
                     "confidence": (0.7 if len(evidence) >= 2 else 0.5) if auto else 1.0})
    for f in files:
        p.link(n, p.file_node(f), "about")
    p.event("pause", "paused: " + (vals.get("next") or ""), vals)
    return cid


def add_memory(p, type_, label, files, **data):
    p.snapshot()
    files = files or p.hot_files(4)
    n = p.node(type_, label, data={**data, "hashes": p.hashes(files)})
    for f in files:
        p.link(n, p.file_node(f), "about")
    return n


def record_cmd(p, text, code, output, exec_id=None):
    """Log a finished command; failures become error memories, repeats surface what is already known."""
    if exec_id:  # the same execution reported twice is recognised by id, never by timing
        seen = json.loads(p.get("seen_exec") or "[]")
        if exec_id in seen:
            return
        p.put("seen_exec", json.dumps((seen + [exec_id])[-100:]))
    eid = p.event("cmd", f"`{text}` -> exit {code}")
    if code != 0:
        sig, line = error_sig(output)
        if not sig:
            return
        row = p.db.execute("SELECT * FROM nodes WHERE type='error' AND key=?", (sig,)).fetchone()
        if row:
            d = p.nd(row)
            before = d.get("last_eid")
            p.update_data(row["id"], count=d.get("count", 1) + 1, status="open", cmd=text, last_ts=now(), last_eid=eid)
            out(f"[rewind] seen this error {d.get('count', 1) + 1}x before.")
            if d.get("solution"):
                out(f"[rewind] previously solved by: {d['solution']}")
            for f in p.db.execute("SELECT n.* FROM nodes n JOIN edges e ON e.src=n.id WHERE e.dst=? AND e.rel='attempt-for'",
                                  (row["id"],)):
                out(f"[rewind] already tried: {f['label']} -- {p.nd(f).get('why') or ''}")
            edited = (p.edited_between(before, eid) if before is not None
                      else p.edited_since(d.get("last_ts") or row["ts"]))
            if edited:  # you changed code and the same error came back: that attempt failed
                names = ", ".join(os.path.basename(f) for f in edited[:3])
                fid = p.auto_node(
                    "failed", f"Edited {names} but same error persisted: {row['label'][:60]}",
                    f"auto-fail:{row['id']}:{eid}", edited,
                    evidence=[f"{len(edited)} file(s) were edited after the error first appeared: {names}",
                              f"the same error occurred again ({d.get('count', 1) + 1}x)", f"command: {text[:80]}"],
                    confidence=0.8 if len(edited) <= 2 else 0.6 if len(edited) <= 5 else 0.45,
                    why="error recurred after these edits",
                    cp=p.checkpoint("attempt", f"Auto: edits did not fix {row['label'][:50]}"))
                if fid:
                    p.link(fid, row["id"], "attempt-for")
                    out(f"[rewind] auto-recorded failed attempt: edits to {', '.join(edited[:3])}")
        else:
            n = add_memory(p, "error", line, None, count=1, status="open", cmd=text, last_ts=now(), last_eid=eid,
                           confidence=1.0, evidence=[f"`{text[:80]}` exited with code {code}"])
            p.db.execute("UPDATE nodes SET key=? WHERE id=?", (sig, n))
            p.db.commit()
            p.checkpoint("bug", f"Error: {line[:80]}")
            out(f"[rewind] new error logged: {line[:80]}")
    else:
        for row in p.db.execute("SELECT * FROM nodes WHERE type='error' AND json_extract(data,'$.status')='open'").fetchall():
            d = p.nd(row)
            if d.get("cmd") == text:
                fixed_by = (p.edited_between(d.get("last_eid"), eid) if d.get("last_eid") is not None
                            else p.edited_since(d.get("last_ts") or row["ts"]))
                p.update_data(row["id"], status="solved", solved_ts=now(), solution=d.get("solution") or (
                    "edited " + ", ".join(fixed_by[:4]) if fixed_by else "(command passes again)"),
                    fix_evidence=[f"`{text[:80]}` failed before and now exits 0"] +
                                 ([f"files edited in between: {', '.join(fixed_by[:4])}"] if fixed_by else []))
                resolve_error(p, row["id"], p.checkpoint("fix", f"Fixed: {row['label'][:70]}"))
                out("[rewind] previously failing command now passes - checkpoint saved")
