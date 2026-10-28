"""Context graph export and structured state."""
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


def graph_template():
    return (Path(__file__).resolve().parent / "assets" / "graph_template.html").read_text(encoding="utf-8")


def build_graph(p, limit=40):
    """Snapshot of the context graph (recent milestones and everything linked to them)."""
    cps = p.db.execute("SELECT * FROM checkpoints ORDER BY id DESC LIMIT ?", (limit,)).fetchall()[::-1]
    data = {"project": p.root.name, "nodes": [], "edges": [], "handoffs": {}, "latest": build_handoff(p)}
    if not cps:
        return data
    first = cps[0]["ts"]
    keys = [str(c["id"]) for c in cps]
    rows = p.db.execute(
        f"SELECT * FROM nodes WHERE (type='checkpoint' AND key IN ({','.join('?' * len(keys))})) "
        "OR (type NOT IN ('file','checkpoint') AND (ts>=? OR type='task'))", (*keys, first)).fetchall()
    ids = {r["id"] for r in rows}
    edges = [dict(s=e["src"], d=e["dst"], rel=e["rel"]) for e in p.db.execute("SELECT * FROM edges")]
    degree = Counter()
    for e in edges:
        if e["s"] in ids:
            degree[e["d"]] += 1
    files = {r["id"]: r for r in p.db.execute(
        f"SELECT * FROM nodes WHERE type='file' AND id IN ({','.join('?' * len(degree))})", tuple(degree))} if degree else {}
    top = {i for i, _ in sorted(((i, degree[i]) for i in files), key=lambda x: -x[1])[:25]}
    keep = ids | top
    for r in list(rows) + [files[i] for i in sorted(top)]:
        d = p.nd(r)
        parts = [f"{k}: {d[k]}" for k in ("why", "hypothesis", "testing", "result", "next", "status", "solution",
                                          "count", "author") if d.get(k) not in (None, "")]
        if d.get("confidence") is not None and d.get("auto"):
            parts.append(f"confidence: {int(float(d['confidence']) * 100)}% (inferred)")
        if d.get("evidence"):
            parts.append("evidence: " + "; ".join(str(x) for x in d["evidence"]))
        info = "\n".join(parts)
        data["nodes"].append({"id": r["id"], "type": r["type"], "label": r["label"][:90], "ts": r["ts"],
                              "cp": int(r["key"]) if r["type"] == "checkpoint" else None, "info": redact(info)})
    data["edges"] = [e for e in edges if e["s"] in keep and e["d"] in keep]
    for c in cps:
        data["handoffs"][c["id"]] = build_handoff(p, c["ts"])
    return data


def export_context(p):
    """Keep a ready-to-paste handoff on disk so any tool can read the latest context without running anything."""
    try:
        (p.dir / "CONTEXT.md").write_text(build_handoff(p), encoding="utf-8")
    except OSError:
        pass


def build_state(p, at=None):
    cut = at or "9999"
    tasks = p.tasks(at)
    cur = next((t for t in reversed(tasks) if not t[2]), None)
    pauses = p.memories("pause", at)

    def meta(nid):
        r = p.db.execute("SELECT data FROM nodes WHERE id=?", (nid,)).fetchone()
        return json.loads(r["data"] or "{}") if r else {}

    def trust(d):
        """Provenance shown for inferred items: how sure Rewind is, and what it is based on."""
        if not d.get("auto"):
            return {"auto": False}
        return {"auto": True, "confidence": d.get("confidence"), "evidence": d.get("evidence") or [],
                "confirmed": bool(d.get("confirmed"))}

    guess = suggest_state(p) if not at and p.activity_since_pause() else None
    stopped = None
    if guess:
        ev = state_evidence(p)
        stopped = {"source": "auto", "ts": p.last_activity_ts(), **guess, "auto": True,
                   "confidence": 0.7 if len(ev) >= 2 else 0.5, "evidence": ev}
    elif pauses:
        d = p.nd(pauses[-1])
        stopped = {"source": "auto" if d.get("auto") else "saved", "ts": pauses[-1]["ts"],
                   **{k: d.get(k) for k in ("hypothesis", "testing", "result", "next")},
                   "stale": p.stale(pauses[-1]), **trust(d)}
    errors = [r for r in p.memories("error", at) if p.nd(r).get("status") == "open"]
    away = None
    if not at:
        raw = p.get("away_report")
        if raw:
            rep = json.loads(raw)
            if rep.get("ts", "") > time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 12 * 3600)):
                away = rep
    done_n = sum(1 for t in tasks if t[2])
    mission = None
    if cur:
        blocked = bool(errors)
        mission = {"id": cur[0], "title": cur[1], "status": "blocked" if blocked else "in_progress",
                   "status_text": (f"Blocked by {len(errors)} open error{'s' if len(errors) > 1 else ''}" if blocked
                                   else "In progress"),
                   "next": (stopped or {}).get("next"), "done": done_n, "total": len(tasks), **trust(meta(cur[0]))}
    return {
        "project": p.root.name, "branch": (p.branch() if p.is_git else None), "is_git": p.is_git, "now": now(),
        "last_active": p.get("last_active"),
        "mission": mission,
        "task": {"id": cur[0], "label": cur[1], "auto": bool(meta(cur[0]).get("auto"))} if cur else None,
        "tasks": [{"id": t[0], "label": t[1], "done": t[2], **trust(meta(t[0]))} for t in tasks][-30:],
        "stopped": stopped,
        "errors": [{"id": r["id"], "label": r["label"], "count": p.nd(r).get("count", 1), "cmd": p.nd(r).get("cmd"),
                    "evidence": p.nd(r).get("evidence") or []} for r in errors][-5:],
        "decisions": [{"id": r["id"], "label": r["label"], "why": p.nd(r).get("why"), "stale": bool(p.stale(r)),
                       "ts": r["ts"], **trust(p.nd(r))} for r in p.memories("decision", at)][-8:],
        "deadends": [{"id": r["id"], "label": r["label"], "why": p.nd(r).get("why"), "cp": p.nd(r).get("cp"),
                      "ts": r["ts"], **trust(p.nd(r))} for r in p.memories("failed", at)][-8:],
        "milestones": [{"id": c["id"], "ts": c["ts"], "kind": c["kind"], "title": c["title"], "branch": c["branch"]}
                       for c in p.db.execute("SELECT * FROM checkpoints WHERE ts<=? ORDER BY id DESC LIMIT 24", (cut,))],
        "files": [x for _, x in p.dirty()][:8] if p.is_git else p.hot_files(8),
        "away": away,
        "context_file": str(p.dir / "CONTEXT.md"),
    }
