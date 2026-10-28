"""MCP stdio server."""
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


# ---- MCP server: lets any MCP-capable AI tool read/write Rewind memory
def cmd_mcp(p, a):
    import contextlib
    import io

    def call(fn, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(p, argparse.Namespace(**kw))
        return buf.getvalue().strip() or "ok"

    S = lambda props, req: {"type": "object", "properties": {k: {"type": "string"} for k in props}, "required": req}  # noqa: E731
    tools = {
        "rewind_context": ("Call FIRST in every session: distilled, secret-free project memory (current task, what was tried, decisions).", S(["focus"], []),
                           lambda x: build_handoff(p, None, 1500, "ai", x.get("focus", ""))),
        "rewind_check_approach": ("Call BEFORE attempting any fix: checks whether an approach was already tried and failed.", S(["approach"], ["approach"]),
                                  lambda x: call(cmd_check, text=x["approach"])),
        "rewind_record_failed": ("Call AFTER an approach fails: record what did NOT work and why.", S(["approach", "why"], ["approach"]),
                                 lambda x: call(cmd_fail, text=x["approach"], why=x.get("why"), file=None)),
        "rewind_record_decision": ("Call after making a design choice: record the decision and why.", S(["decision", "why"], ["decision"]),
                                   lambda x: call(cmd_decide, text=x["decision"], why=x.get("why"), file=None)),
        "rewind_trace": ("Explain why a file, error or topic is the way it is: the chain of attempts, decisions and fixes.",
                         S(["query"], ["query"]), lambda x: trace(p, x["query"])),
        "rewind_ask": ("Ask about project history.", S(["question"], ["question"]),
                       lambda x: call(cmd_ask, question=x["question"], at=None)),
    }
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:
            continue
        res, err = {}, None
        if method == "initialize":
            res = {"protocolVersion": params.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                   "serverInfo": {"name": "rewind", "version": "0.7.0"}, "instructions": AGENT_RULES}
        elif method == "tools/list":
            res = {"tools": [{"name": n, "description": d, "inputSchema": sch} for n, (d, sch, _) in tools.items()]}
        elif method == "tools/call":
            t = tools.get(params.get("name"))
            if not t:
                err = {"code": -32602, "message": "unknown tool"}
            else:
                try:
                    res = {"content": [{"type": "text", "text": t[2](params.get("arguments") or {})}]}
                except Exception as e:  # report to the client instead of killing the server
                    res = {"isError": True, "content": [{"type": "text", "text": str(e)}]}
        elif method != "ping":
            err = {"code": -32601, "message": "method not found"}
        reply = {"jsonrpc": "2.0", "id": mid, **({"error": err} if err else {"result": res})}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
