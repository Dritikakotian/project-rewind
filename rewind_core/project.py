"""Project: git/file capture, graph nodes, checkpoints."""
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


# ---------------------------------------------------------------- project
class Project:
    def __init__(self, path):
        path = Path(path).resolve()
        top = self._git(path, "rev-parse", "--show-toplevel").strip()
        self.root = Path(top) if top else path
        self.is_git = bool(top)
        home = rewind_home()
        pid = f"{self.root.name}-{hashlib.sha1(str(self.root).encode()).hexdigest()[:8]}"
        self.dir = home / "projects" / pid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = open_db(self.dir / "rewind.db")
        self.ignore = DEFAULT_IGNORE + self._read_ignore()

    # ---- low level
    @staticmethod
    def _git(cwd, *args, inp=None):
        try:
            r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", input=inp)
            return r.stdout if r.returncode == 0 else ""
        except OSError:
            return ""

    def git_raw(self, *args):
        """git stdout with every byte preserved (latin-1 round trip): CRLF and non-UTF-8 content survive."""
        try:
            r = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True)
            return r.stdout.decode("latin-1") if r.returncode == 0 else ""
        except OSError:
            return ""

    def git(self, *args, inp=None):
        return self._git(self.root, *args, inp=inp) if self.is_git else ""

    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (k, None if v is None else str(v)))
        self.db.commit()

    def _read_ignore(self):
        f = self.root / ".rewindignore"
        if not f.exists():
            return []
        return [l.strip() for l in f.read_text(encoding="utf-8", errors="replace").splitlines()
                if l.strip() and not l.startswith("#")]

    def is_ignored(self, rel):
        rel = rel.replace("\\", "/")
        parts = rel.split("/")
        for pat in self.ignore:
            if pat.endswith("/"):
                if pat[:-1] in parts[:-1]:
                    return True
            elif fnmatch.fnmatch(rel, pat) or any(fnmatch.fnmatch(p, pat) for p in parts):
                return True
        return False

    # ---- graph
    def node(self, type_, label, key=None, data=None, ts=None):
        """Create (or fetch, when key is given) a graph node and return its id."""
        if key is not None:
            r = self.db.execute("SELECT id FROM nodes WHERE type=? AND key=?", (type_, key)).fetchone()
            if r:
                return r["id"]
        try:
            cur = self.db.execute("INSERT INTO nodes(type,key,label,data,ts) VALUES(?,?,?,?,?)",
                                  (type_, key, redact(label), json.dumps(data or {}), ts or now()))
        except sqlite3.IntegrityError:
            # another editor event created the same memory a moment ago: use that one
            r = self.db.execute("SELECT id FROM nodes WHERE type=? AND key=?", (type_, key)).fetchone()
            if r is None:
                raise
            return r["id"]
        self.db.commit()
        return cur.lastrowid

    def link(self, src, dst, rel):
        self.db.execute("INSERT OR IGNORE INTO edges VALUES(?,?,?)", (src, dst, rel))
        self.db.commit()

    def file_node(self, rel):
        return self.node("file", rel, key=rel)

    def nd(self, row):
        return json.loads(row["data"] or "{}")

    def update_data(self, nid, **kw):
        row = self.db.execute("SELECT data FROM nodes WHERE id=?", (nid,)).fetchone()
        d = json.loads(row["data"] or "{}")
        d.update(kw)
        self.db.execute("UPDATE nodes SET data=? WHERE id=?", (json.dumps(d), nid))
        self.db.commit()

    def event(self, kind, summary, data=None):
        self.db.execute("INSERT INTO events(ts,kind,summary,data) VALUES(?,?,?,?)",
                        (now(), kind, redact(summary), json.dumps(data or {})))
        eid = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.db.commit()
        self.put("last_active", now())
        return eid  # ordering by event id (not clock time) keeps inference deterministic

    # ---- git / files
    def branch(self):
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def dirty(self):
        """Non-ignored changed paths from git status (git keeps this incremental)."""
        raw = self.git("status", "--porcelain=v1", "-z", "-uall")
        entries, res, skip = raw.split("\0"), [], False
        for e in entries:
            if skip:
                skip = False
                continue
            if len(e) < 4:
                continue
            code, path = e[:2], e[3:]
            if code[0] in "RC":
                skip = True
            if not self.is_ignored(path):
                res.append((code, path))
        return res

    def file_hash(self, rel):
        p = self.root / rel
        try:
            if p.stat().st_size > 2_000_000:
                return f"big-{p.stat().st_size}"
            return hashlib.sha1(p.read_bytes()).hexdigest()[:12]
        except OSError:
            return "missing"

    def hot_files(self, limit=6):
        c = Counter()
        for code, p in self.dirty():
            c[p] += 100
        rows = self.db.execute("SELECT data FROM events WHERE kind='edit' ORDER BY id DESC LIMIT 40").fetchall()
        for i, r in enumerate(rows):
            for f in json.loads(r["data"]).get("files", []):
                c[f] += 1 + (40 - i) / 40
        return [f for f, _ in c.most_common(limit) if not self.is_ignored(f)]

    def hashes(self, files):
        return {f: self.file_hash(f) for f in files if not self.is_ignored(f)}

    def maybe_gc(self):
        """Weekly housekeeping so months of activity stay small and fast."""
        last = self.get("last_gc")
        if last is None:
            self.put("last_gc", now())
        elif last < time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7 * 86400)):
            gc(self.db)
            self.put("last_gc", now())

    # ---- automatic memory (nothing to type)
    def auto_node(self, type_, label, key, files, evidence=None, confidence=0.6, **data):
        """Create an inferred memory once (idempotent by key); returns node id or None if it already existed."""
        if self.db.execute("SELECT 1 FROM nodes WHERE type=? AND key=?", (type_, key)).fetchone():
            return None
        files = [f for f in (files or []) if not self.is_ignored(f)][:6]
        n = self.node(type_, label, key=key, data={**data, "auto": True, "evidence": evidence or [], "confidence": confidence,
                                                 "hashes": self.hashes(files)})
        for f in files:
            self.link(n, self.file_node(f), "about")
        if type_ == "decision":
            from .reasoning import link_reasoning  # imported lazily: reasoning sits above this module
            link_reasoning(self, n, label + " " + str(data.get("why") or ""))
        return n

    def last_event_id(self):
        return self.db.execute("SELECT MAX(id) m FROM events WHERE kind!='pause'").fetchone()["m"] or 0

    def activity_since_pause(self):
        """True when something happened after the latest pause (compared by event order, not seconds)."""
        pauses = self.memories("pause")
        if not pauses:
            return self.last_event_id() > 0
        eid = self.nd(pauses[-1]).get("eid")
        return self.last_event_id() > eid if eid is not None else self.last_activity_ts() > pauses[-1]["ts"]

    def edited_between(self, after_eid, before_eid):
        """Files edited in the events strictly between two event ids."""
        files = []
        for r in self.db.execute("SELECT data FROM events WHERE kind='edit' AND id>? AND id<? ORDER BY id",
                                 (after_eid or 0, before_eid)):
            for f in json.loads(r["data"]).get("files", []):
                if f not in files and not self.is_ignored(f):
                    files.append(f)
        return files

    def last_activity_ts(self):
        r = self.db.execute("SELECT MAX(ts) m FROM events WHERE kind!='pause'").fetchone()
        return r["m"] or ""

    def edited_since(self, ts):
        files = []
        for r in self.db.execute("SELECT data FROM events WHERE kind='edit' AND ts>=? ORDER BY id", (ts or "",)):
            for f in json.loads(r["data"]).get("files", []):
                if f not in files and not self.is_ignored(f):
                    files.append(f)
        return files

    def ensure_task(self, hint_files):
        """Infer the current task from the branch name or the files being edited when none is set."""
        branch = self.branch() if self.is_git else ""
        named = branch and branch not in ("main", "master", "HEAD")
        tid = self.get("task_id")
        if tid:
            row = self.db.execute("SELECT data FROM nodes WHERE id=?", (tid,)).fetchone()
            d = json.loads(row["data"] or "{}") if row else {}
            if not (d.get("auto") and named and d.get("branch") != branch):
                return
        if named:
            label = "Work on " + re.sub(r"[-_/]+", " ", re.sub(r"^(feature|feat|fix|bugfix|hotfix|chore)/", "", branch))
        elif hint_files:
            base = [os.path.basename(f) for f in hint_files]
            label = f"Editing {base[0]}" if len(set(hint_files)) == 1 else (
                "Editing " + (os.path.commonpath([os.path.dirname(f) or "." for f in hint_files]) or ".") +
                f" ({len(hint_files)} files)")
        else:
            return
        if self.db.execute("SELECT 1 FROM nodes WHERE type='task' AND label=? AND json_extract(data,'$.dismissed')=1",
                           (label,)).fetchone():
            return
        evidence = ([f"current branch is '{branch}'"] if named else
                    [f"you edited {len(hint_files)} file(s): {', '.join(os.path.basename(f) for f in hint_files[:3])}"])
        n = self.node("task", label, data={"status": "open", "auto": True, "branch": branch,
                                            "evidence": evidence, "confidence": 0.8 if named else 0.5})
        self.put("task_id", n)
        self.checkpoint("start", f"Started (auto): {label}", task=n)

    COMMENT_TODO = re.compile(r"(?://|#|/\*|<!--|--)\s*(TODO|FIXME|XXX)\b[:\s-]*(.*)")
    COMMENT_NOTE = re.compile(r"(?://|#|/\*|<!--|--)\s*(NOTE|HACK|WORKAROUND|WHY|IMPORTANT)\b[:\s-]*(.*)")

    def scan_comments(self, rels):
        """TODO/FIXME comments become tasks (done when the comment disappears); NOTE/HACK/WHY become decisions."""
        for rel in rels[:10]:
            f = self.root / rel
            try:
                if not f.is_file() or f.stat().st_size > 100_000:
                    continue
                lines = f.read_bytes().decode("utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            present = set()
            for line in lines:
                m = self.COMMENT_TODO.search(line)
                if m and m.group(2).strip():
                    text = m.group(2).strip().rstrip("*/-> ")[:80]
                    key = f"todo:{rel}:{hashlib.sha1(text.encode()).hexdigest()[:8]}"
                    present.add(key)
                    if not self.db.execute("SELECT 1 FROM nodes WHERE type='task' AND key=?", (key,)).fetchone():
                        self.node("task", f"{m.group(1)}: {text} ({os.path.basename(rel)})", key=key,
                                  data={"status": "open", "auto": True, "file": rel, "confidence": 0.9,
                                        "evidence": [f"{m.group(1)} comment in {rel}"]})
                    continue
                m = self.COMMENT_NOTE.search(line)
                if m and len(m.group(2).strip()) > 8:
                    text = m.group(2).strip().rstrip("*/-> ")[:120]
                    self.auto_node("decision", text, f"note:{rel}:{hashlib.sha1(text.encode()).hexdigest()[:8]}",
                                   [rel], evidence=[f"{m.group(1)} comment in {rel}"], confidence=0.7,
                                   why=f"{m.group(1).lower()} comment in {rel}")
            for row in self.db.execute("SELECT id, key, data FROM nodes WHERE type='task' AND key LIKE ?", (f"todo:{rel}:%",)).fetchall():
                d = json.loads(row["data"] or "{}")
                if d.get("status") == "open" and row["key"] not in present:
                    self.update_data(row["id"], status="done", done_ts=now())

    # ---- checkpoints
    def snapshot_files(self, rels):
        """Small text files stored with a checkpoint so Resume can restore them (untracked / non-git)."""
        files, total = {}, 0
        for rel in rels:
            f = self.root / rel
            try:
                if self.is_ignored(rel) or not f.is_file() or f.stat().st_size > 100_000 or len(files) >= 20:
                    continue
                text = f.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            total += len(text)
            if total > 500_000:
                break
            files[rel] = text
        return files

    def checkpoint(self, kind, title, task=None):
        dirty = [p for _, p in self.dirty()]
        tracked = [p for c, p in self.dirty() if c != "??"]
        if self.is_git:
            untracked = self.snapshot_files([p for c, p in self.dirty() if c == "??"])
        else:
            dirty = self.hot_files(12)
            untracked = self.snapshot_files(dirty)
        patch = ""
        if tracked and self.git("rev-parse", "--verify", "-q", "HEAD"):
            patch = self.git_raw("diff", "HEAD", "--binary", "--", *tracked)
            if len(patch) > 500_000:
                patch = ""
        task = task if task is not None else (int(self.get("task_id")) if self.get("task_id") else None)
        cur = self.db.execute(
            "INSERT INTO checkpoints(ts,kind,title,branch,head,task,dirty,patch,untracked) VALUES(?,?,?,?,?,?,?,?,?)",
            (now(), kind, redact(title), self.branch(), self.head(), task, json.dumps(dirty), seal(patch),
             seal(json.dumps(untracked))))
        self.db.commit()
        cid = cur.lastrowid
        n = self.node("checkpoint", f"#{cid} [{kind}] {title}", key=str(cid))
        for f in dirty[:30]:
            self.link(n, self.file_node(f), "touches")
        if task:
            self.link(task, n, "has")
        self.put("last_checkpoint", cid)
        return cid

    # ---- capture
    def scan_files(self):
        """Non-git fallback: walk the project, pruning ignored directories (stat only, no reads)."""
        res = {}
        for dp, dns, fns in os.walk(self.root):
            rel_dir = os.path.relpath(dp, self.root).replace("\\", "/")
            base = "" if rel_dir == "." else rel_dir + "/"
            dns[:] = [d for d in dns if not self.is_ignored(f"{base}{d}/_")]
            for fn in fns:
                rel = base + fn
                if not self.is_ignored(rel):
                    try:
                        res[rel] = (Path(dp) / fn).stat().st_mtime
                    except OSError:
                        pass
        return res

    def snapshot(self, files=None):
        """Incremental capture. `files` = explicit paths (editor save events); otherwise git status,
        or a pruned mtime walk for non-git folders. Commits and branch changes always come from git."""
        notes = []
        prev_raw = self.get("mtimes")
        prev = json.loads(prev_raw or "{}")
        changed = []
        if files is not None:
            cur = dict(prev)
            for f in files:
                rel = os.path.relpath(f, self.root).replace("\\", "/") if os.path.isabs(f) else f.replace("\\", "/")
                if rel.startswith("..") or self.is_ignored(rel):
                    continue
                try:
                    m = (self.root / rel).stat().st_mtime
                except OSError:
                    m = 0
                cur[rel] = m
                if prev.get(rel) != m:
                    changed.append(rel)
        else:
            cur = {}
            if self.is_git:
                for code, p in self.dirty():
                    try:
                        cur[p] = (self.root / p).stat().st_mtime
                    except OSError:
                        cur[p] = 0
            else:
                cur = self.scan_files()
            changed = [p for p, m in cur.items() if prev.get(p) != m]
            if not self.is_git and not prev:
                changed = []  # first scan only sets the baseline
        if changed:
            self.event("edit", f"edited {len(changed)} file(s): " + ", ".join(changed[:5]), {"files": changed})
            notes.append(f"{len(changed)} file edit(s)")
            self.scan_comments(changed)
        self.put("mtimes", json.dumps(cur))
        self.maybe_gc()
        if changed and not self.is_git:
            self.ensure_task(changed)
        if not self.is_git:
            return notes
        if changed:
            self.ensure_task(changed)
        branch, head = self.branch(), self.head()
        last_branch, last_head = self.get("last_branch"), self.get("last_head")
        if branch and last_branch and branch != last_branch:
            self.event("branch", f"switched {last_branch} -> {branch}")
            notes.append(f"branch {branch}")
        if branch and branch != last_branch and branch not in ("main", "master", "HEAD") and last_branch is not None:
            self.checkpoint("start", f"Started work on branch {branch}")
        if head and head != last_head:
            rng = f"{last_head}..{head}" if last_head and self.git("cat-file", "-t", last_head).strip() else "-n3"
            args = ["log", "--reverse", "--format=%H%x1f%an%x1f%s"] + ([rng] if rng != "-n3" else ["-n3"])
            for line in self.git(*args).splitlines():
                h, author, subj = (line.split("\x1f") + ["", ""])[:3]
                files = [f for f in self.git("show", "--name-only", "--format=", h).split() if not self.is_ignored(f)]
                n = self.node("commit", f"{h[:8]} {subj}", key=h, data={"author": author})
                for f in files:
                    self.link(n, self.file_node(f), "touches")
                self.event("commit", f"{h[:8]} {subj}", {"files": files})
                self.checkpoint("commit", f"{h[:8]} {subj}")
                notes.append(f"commit {h[:8]}")
                body = self.git("show", "-s", "--format=%b", h).strip()
                if re.match(r"(?i)revert", subj):
                    self.auto_node("failed", f"Reverted: {subj[:80]}", f"revert:{h}", files,
                               evidence=[f"commit {h[:8]} is a revert: '{subj[:60]}'"], confidence=0.8,
                               why="commit was reverted", cp=None)
                elif body or DECISION_RE.search(subj):
                    self.auto_node("decision", subj[:100], f"commit-decision:{h}", files,
                               evidence=[f"commit {h[:8]} message body explains the change" if body else
                                         f"commit {h[:8]} subject mentions a reason (because / instead of / switch to ...)"],
                               confidence=0.85 if body else 0.55, why=(body or subj)[:300])
                if last_head:  # the very first scan must not guess a task from old history
                    self.ensure_task(files)
        self.put("last_branch", branch)
        self.put("last_head", head)
        return notes

    # ---- views
    def away(self):
        """What changed in git since the last captured state (before snapshot advances it)."""
        if not self.is_git:
            return None
        last, head = self.get("last_head"), self.head()
        res = {"commits": [], "deps": [], "merges": [], "files": []}
        if not last or last == head or not self.git("cat-file", "-t", last).strip():
            return res
        me = self.git("config", "user.name").strip()
        for line in self.git("log", "--reverse", f"{last}..{head}", "--format=%h%x1f%an%x1f%s").splitlines():
            h, a, s = (line.split("\x1f") + ["", ""])[:3]
            res["commits"].append((h, a, s, a == me))
            if s.startswith(("Merge pull request", "Merge branch")):
                res["merges"].append(f"{h} {s}")
        files = [f for f in self.git("diff", "--name-only", last, head).split() if not self.is_ignored(f)]
        res["files"] = files
        res["deps"] = [f for f in files if os.path.basename(f) in DEP_FILES]
        return res

    def tasks(self, at=None):
        rows = self.db.execute("SELECT * FROM nodes WHERE type='task' AND ts<=? AND json_extract(data,'$.dismissed') IS NOT 1 "
                               "ORDER BY id", (at or "9999",)).fetchall()
        res = []
        for r in rows:
            d = self.nd(r)
            done = d.get("status") == "done" and d.get("done_ts", "") <= (at or "9999")
            res.append((r["id"], r["label"], done))
        return res

    def memories(self, type_, at=None):
        return self.db.execute("SELECT * FROM nodes WHERE type=? AND ts<=? AND json_extract(data,'$.dismissed') IS NOT 1 "
                               "ORDER BY id", (type_, at or "9999")).fetchall()

    def linked_files(self, nid):
        return {r["label"] for r in self.db.execute(
            "SELECT n.label FROM nodes n JOIN edges e ON e.dst=n.id WHERE e.src=? AND n.type='file'", (nid,))}

    def stale(self, row):
        """Files related to a memory whose content changed since it was written."""
        hs = self.nd(row).get("hashes", {})
        return [f for f, h in hs.items() if self.file_hash(f) != h]

    def stale_note(self, row):
        s = self.stale(row)
        if not s:
            return ""
        return f"  (!) may no longer be valid - {len(s)} related file(s) changed since created: {', '.join(s[:3])}"
