"""Relevance-ranked, redacted context handoff."""
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


# ---- handoff / firewall
def rank_memories(p, rows, focus, hot, keep=6, focus_text=""):
    """Keep memories relevant to the current focus/files (plus the 2 newest); report how many were dropped."""
    scored = []
    for i, r in enumerate(rows):
        d = p.nd(r)
        score = 2 * len(focus & tokens(r["label"] + " " + (d.get("why") or "")))
        score += 3 * len(p.linked_files(r["id"]) & hot)
        if focus_text:
            score += int(6 * similarity(focus_text, r["label"] + " " + (p.nd(r).get("why") or "")))
        if p.stale(r):
            score -= 2
        scored.append((score, i, r))
    newest = {r["id"] for r in rows[-2:]}
    kept = [x for x in scored if x[0] > 0 or x[2]["id"] in newest]
    kept = sorted(kept, key=lambda x: (-x[0], -x[1]))[:keep]
    return [x[2] for x in sorted(kept, key=lambda x: x[1])], len(rows) - len(kept)


def build_handoff(p, at=None, budget=1500, target="ai", focus=""):
    """Minimum-context handoff: relevance-ranked, stale-flagged, redacted, token-budgeted, no code."""
    cut = at or "9999"
    tasks = p.tasks(at)
    cur = next((t for t in reversed(tasks) if not t[2]), None)
    pauses = p.memories("pause", at)
    pd = p.nd(pauses[-1]) if pauses else {}
    auto_state = None
    if not at and p.activity_since_pause():
        auto_state = suggest_state(p)
        pd = auto_state or pd
    hot = set(p.hot_files(8)) if not at else set()
    focus_t = tokens(" ".join([cur[1] if cur else "", focus, " ".join(str(v) for v in pd.values() if isinstance(v, str)),
                               " ".join(re.sub(r"[/._-]", " ", f) for f in hot)]))
    omitted = 0
    sections = []

    def add(title, lines):
        if lines:
            sections.append((title, lines))

    def ranked(type_):
        nonlocal omitted
        rows, dropped = rank_memories(p, p.memories(type_, at), focus_t, hot, focus_text=" ".join(sorted(focus_t)))
        omitted += dropped
        return rows

    add("Current task", [cur[1]] if cur else [])
    if pauses or auto_state:
        add("Where work stopped" + (" (auto-detected)" if auto_state else ""), [f"hypothesis: {pd.get('hypothesis') or '-'}", f"was testing: {pd.get('testing') or '-'}",
                                   f"last result: {pd.get('result') or '-'}", f"next intended: {pd.get('next') or '-'}"])
    add("Already tried and FAILED (do not repeat)",
        [f"{r['label']} -- {p.nd(r).get('why') or 'reason not recorded'}" for r in ranked("failed")])
    add("Decisions", [f"{r['label']} -- {p.nd(r).get('why') or ''}{' [POSSIBLY STALE]' if p.stale(r) else ''}"
                      for r in ranked("decision")])
    add("Open errors", [f"{r['label']} (x{p.nd(r).get('count', 1)})" for r in p.memories("error", at)
                        if p.nd(r).get("status") == "open"][-4:])
    add("Remaining tasks", [t[1] for t in tasks if not t[2]])
    add("Completed", [t[1] for t in tasks if t[2]][-4:])
    cps = p.db.execute("SELECT * FROM checkpoints WHERE ts<=? ORDER BY id DESC LIMIT 5", (cut,)).fetchall()
    add("Recent milestones", [f"#{c['id']} {c['kind']}: {c['title'][:80]}" for c in cps])
    if hot:
        add("Files in play (names only, no code)", sorted(hot)[:6])

    head = ("You are continuing an in-progress software task. Context below is distilled project memory; "
            "do not repeat failed approaches.\n" if target == "ai" else "Handoff note for the next developer.\n")
    text = f"# Rewind handoff: {p.root.name}\n{head}"
    for title, lines in sections:
        block = f"\n## {title}\n" + "\n".join(f"- {l}" for l in lines) + "\n"
        if (len(text) + len(block)) / 4 > budget:
            omitted += len(lines)
            continue
        text += block
    if omitted:
        text += f"\n(Firewall omitted {omitted} older/unrelated item(s); ask for more with --focus.)\n"
    return redact(text)
