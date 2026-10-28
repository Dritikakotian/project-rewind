"""Heuristic over-engineering checks."""
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


def _depth(node, level=0):
    import ast
    deeper = (ast.If, ast.For, ast.While, ast.Try, ast.With)
    best = level
    for ch in ast.iter_child_nodes(node):
        best = max(best, _depth(ch, level + 1) if isinstance(ch, deeper) else _depth(ch, level))
    return best


def simplify_python(src):
    import ast
    tree = ast.parse(src)
    tips = []
    imported = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                imported[(al.asname or al.name).split(".")[0]] = n.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    for name, line in imported.items():
        if name not in used and name != "*":
            tips.append((line, f"unused import '{name}'", "remove it"))
    for n in ast.walk(tree):
        line = getattr(n, "lineno", 0)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            size = (n.end_lineno or line) - line + 1
            if size > 50:
                tips.append((line, f"function '{n.name}' is {size} lines", "split into smaller helpers"))
            if len(n.args.args) > 6:
                tips.append((line, f"'{n.name}' takes {len(n.args.args)} parameters", "group them in a dataclass/dict"))
            if _depth(n) > 4:
                tips.append((line, f"'{n.name}' nests {_depth(n)} levels deep", "use early returns / extract blocks"))
            body = [b for b in n.body if not (isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant))]
            if len(body) == 1 and isinstance(body[0], ast.Return) and isinstance(body[0].value, ast.Call) and \
                    [getattr(a_, "id", None) for a_ in body[0].value.args] == [a_.arg for a_ in n.args.args] and not n.decorator_list:
                tips.append((line, f"'{n.name}' only forwards its arguments to another call", "call the target directly"))
        if isinstance(n, ast.If) and len(n.body) == 1 and len(n.orelse) == 1 and \
                all(isinstance(x, ast.Return) and isinstance(x.value, ast.Constant) and isinstance(x.value.value, bool)
                    for x in (n.body[0], n.orelse[0])):
            tips.append((line, "if/else returning True/False", "return the condition directly"))
        if isinstance(n, ast.Compare) and any(isinstance(c, ast.Constant) and c.value in (True, False) and
                                              isinstance(c.value, bool) for c in n.comparators) \
                and any(isinstance(o, (ast.Eq, ast.NotEq)) for o in n.ops):
            tips.append((line, "comparison to True/False", "use the value (or `not value`) directly"))
        if isinstance(n, ast.For) and isinstance(n.iter, ast.Call) and getattr(n.iter.func, "id", "") == "range" and \
                n.iter.args and isinstance(n.iter.args[0], ast.Call) and getattr(n.iter.args[0].func, "id", "") == "len":
            tips.append((line, "for i in range(len(x))", "iterate directly or use enumerate()"))
        if isinstance(n, ast.ExceptHandler) and len(n.body) == 1 and isinstance(n.body[0], ast.Pass):
            tips.append((line, "except that silently passes", "handle or at least log the error"))
        if isinstance(n, ast.If) and n.orelse and n.body and isinstance(n.body[-1], (ast.Return, ast.Raise, ast.Continue)):
            tips.append((line, "else after return/raise/continue", "drop the else and dedent"))
    return tips


def simplify_generic(src):
    """Language-agnostic: repeated 4-line blocks and very deep brace nesting."""
    tips, seen = [], {}
    lines = [(i + 1, l.strip()) for i, l in enumerate(src.splitlines())
             if l.strip() and not l.strip().startswith(("//", "#", "*", "/*")) and l.strip() not in "{}()[];"]
    for k in range(len(lines) - 3):
        key = hashlib.sha1("\n".join(l for _, l in lines[k:k + 4]).encode()).hexdigest()
        first = seen.setdefault(key, lines[k][0])
        if first != lines[k][0] and lines[k][0] - first > 4:
            tips.append((lines[k][0], f"4-line block repeats lines {first}+", "extract a shared function/widget"))
            if len(tips) >= 6:
                break
    depth = 0
    for i, l in enumerate(src.splitlines(), 1):
        depth = max(0, depth + l.count("{") - l.count("}"))
        if depth >= 7:
            tips.append((i, f"nesting depth {depth}", "extract nested blocks into named functions/widgets"))
            break
    return tips
