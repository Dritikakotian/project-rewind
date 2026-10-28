"""Shared constants and small helpers."""
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



DEFAULT_IGNORE = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
    "secrets*", "credentials*", ".git/", "node_modules/", "__pycache__/", "*.pyc", ".venv/",
    "build/", ".dart_tool/", ".idea/", ".gradle/", "dist/", "target/", ".next/", "*.rewind-cp*", "*.lock",
]


DEP_FILES = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt",
    "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock", "Cargo.toml", "Cargo.lock",
    "go.mod", "go.sum", "Gemfile", "Gemfile.lock", "pom.xml", "build.gradle", "composer.json",
}


SECRET_RES = [
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|passwd|password|pwd|auth)\b(\"?'?\s*[:=]\s*)(\"?'?)[^\s\"']{4,}"),
     r"\1\2\3[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{16,}"), "Bearer [REDACTED]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[REDACTED KEY]"),
]


DECISION_RE = re.compile(r"(?i)\b(because|instead of|switch(?:ed)? to|replac(?:e|ed|es)|migrat\w+|workaround|rather than|no longer)\b")


MEMORY_TYPES = ("decision", "failed", "pause", "error")


# ---------------------------------------------------------------- helpers
def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(ts):
    try:
        secs = (datetime.now(timezone.utc) - datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return "?"
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{int(secs // n)}{unit} ago"
    return "just now"


def redact(text):
    for rx, rep in SECRET_RES:
        text = rx.sub(rep, text or "")
    return text


def out(s=""):
    print(s)


def tokens(text):
    return {t for t in re.findall(r"[a-z0-9_]+", (text or "").lower()) if len(t) > 2}


def error_sig(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return "", ""
    hit = [l for l in lines if re.search(r"(?i)error|exception|fail|traceback|panic", l)]
    line = (hit or lines)[-1]
    sig = re.sub(r"[A-Za-z]:\\\S+|/\S+", "PATH", line.lower())
    sig = re.sub(r"0x[0-9a-f]+|\d+", "N", sig)
    return sig[:160], redact(line)[:200]


AGENT_RULES = (
    "This project keeps persistent memory in Rewind. Before changing code or debugging: "
    "(1) call rewind_context to see the current task, what was already tried, and decisions; "
    "(2) before trying a fix, call rewind_check_approach and do not repeat a recorded dead end; "
    "(3) when an approach fails call rewind_record_failed, and when you make a design choice call rewind_record_decision; "
    "(4) call rewind_trace to learn why a file, error or decision is the way it is."
)
