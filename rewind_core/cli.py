"""Argument parser and entry point."""
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
from .commands import *  # noqa: F401,F403
from .mcp_server import *  # noqa: F401,F403


# ---------------------------------------------------------------- cli
def build_parser():
    ap = argparse.ArgumentParser(prog="rewind", description="Project Rewind: never lose your working context.")
    ap.add_argument("--project", default=".", help="project path (default: cwd)")
    sub = ap.add_subparsers(dest="cmd")

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    add("show", cmd_show, "instant rewind + while-you-were-away (default)")
    add("capture", cmd_capture, "record new edits/commits now").add_argument("--file", nargs="*", help="only these paths")
    w = add("watch", cmd_watch, "capture continuously (cheap polling of git status)")
    w.add_argument("--interval", type=int, default=5)
    t = add("task", cmd_task, "set, list, or finish the current task")
    t.add_argument("text", nargs="?")
    t.add_argument("--done", action="store_true")
    add("checkpoint", cmd_checkpoint, "manual milestone").add_argument("title")
    s = add("pause", cmd_pause, "interruption mode: save hypothesis/testing/result/next")
    for k in ("hypothesis", "testing", "result", "next"):
        s.add_argument("--" + k)
    d = add("decide", cmd_decide, "record a decision and why")
    d.add_argument("text")
    d.add_argument("--why")
    d.add_argument("--file", action="append")
    f = add("fail", cmd_fail, "record an approach that did not work")
    f.add_argument("text")
    f.add_argument("--why")
    f.add_argument("--file", action="append")
    add("solved", cmd_solved, "record the fix for the latest open error").add_argument("text")
    r = add("run", cmd_run, "run a command; capture its outcome and errors")
    r.add_argument("command", nargs=argparse.REMAINDER)
    rc = add("record", cmd_record, "log a finished command (output on stdin)")
    rc.add_argument("--command", required=True)
    rc.add_argument("--exit", type=int, default=0)
    rc.add_argument("--id", help="unique id of this execution; a repeated id is ignored")
    add("simplify", cmd_simplify, "heuristic over-engineering suggestions for a file").add_argument("file")
    st = add("state", cmd_state, "structured project state as JSON (for UIs)")
    st.add_argument("--at", type=int)
    add("dismiss-away", cmd_dismiss_away, "hide the while-you-were-away card")
    add("diag", cmd_diag, "ingest editor problems from stdin (JSON)")
    add("autopause", cmd_autopause, "save an inferred pause if there was activity since the last one")
    add("suggest", cmd_suggest, "print the inferred pause state as JSON")
    g = add("graph", cmd_graph, "export the context graph (html viewer, json, or mermaid)")
    g.add_argument("--format", choices=("html", "json", "mermaid"), default="html")
    g.add_argument("--limit", type=int, default=40, help="number of recent milestones")
    g.add_argument("-o", "--output")
    tr = add("trace", cmd_trace, "why is this file/error/topic the way it is (reasoning chain)")
    tr.add_argument("query")
    tr.add_argument("--depth", type=int, default=3)
    gcp = add("gc", cmd_gc, "clean up old events and heavy payloads, then compact the database")
    gcp.add_argument("--keep-patches", type=int, default=30)
    gcp.add_argument("--days", type=int, default=90)
    ag = add("agent-setup", cmd_agent_setup, "make AI coding agents consult Rewind first (prints; --write appends)")
    ag.add_argument("--target", choices=sorted(AGENT_FILES), default="agents")
    ag.add_argument("--write", action="store_true")
    add("confirm", cmd_confirm, "mark an inferred memory as correct").add_argument("id", type=int)
    add("dismiss", cmd_dismiss, "mark an inferred memory as wrong (hidden, not re-inferred)").add_argument("id", type=int)
    add("mcp", cmd_mcp, "run as an MCP stdio server for AI tools")
    add("check", cmd_check, "warn if an approach was already tried and failed").add_argument("text")
    add("failed", cmd_failed, "list failed paths and known errors")
    add("why", cmd_why, "decisions/commits behind a file").add_argument("file")
    add("ignore", cmd_ignore, "exclude a path/pattern from capture").add_argument("pattern")
    add("at", cmd_at, "time machine: what was known at a checkpoint").add_argument("checkpoint", type=int)
    c = add("checkpoints", cmd_checkpoints, "list milestones")
    c.add_argument("-n", type=int, default=15)
    rs = add("resume", cmd_resume, "restore branch/files/task from a checkpoint")
    rs.add_argument("checkpoint", type=int)
    rs.add_argument("--apply", action="store_true")
    h = add("handoff", cmd_handoff, "minimal, redacted context for an AI or developer")
    h.add_argument("--at", type=int, help="checkpoint id")
    h.add_argument("--budget", type=int, default=1500, help="token budget")
    h.add_argument("--target", choices=("ai", "dev"), default="ai")
    h.add_argument("--focus", default="", help="what the AI will work on; boosts related memories")
    h.add_argument("-o", "--output")
    q = add("ask", cmd_ask, "ask about the project's history")
    q.add_argument("question")
    q.add_argument("--at", type=int, help="checkpoint id")
    add("projects", cmd_projects, "list all projects with memory")
    add("hook", cmd_hook, "install git hooks that call capture")
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()
    cmds = {"show", "capture", "watch", "task", "checkpoint", "pause", "decide", "fail", "solved", "run", "check",
            "failed", "why", "ignore", "at", "checkpoints", "resume", "handoff", "ask", "projects", "hook",
            "record", "simplify", "mcp", "graph", "autopause", "suggest", "state", "dismiss-away", "diag", "trace", "gc", "agent-setup", "confirm", "dismiss"}
    if not any(x in cmds for x in argv):
        argv.append("show")
    a = ap.parse_args(argv)
    p = Project(a.project)
    try:
        return a.fn(p, a) or 0
    finally:
        p.db.close()
