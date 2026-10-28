import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import rewind  # noqa: E402

SCRIPT = str(ROOT / "rewind.py")


def git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


class Base(unittest.TestCase):
    """A throwaway git repo plus an isolated Rewind home for every test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        os.environ["REWIND_HOME"] = str(self.home)
        os.environ.pop("REWIND_ENCRYPT", None)
        self.repo = str(Path(self.tmp.name) / "proj")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Me")
        git(self.repo, "config", "user.email", "me@x.io")
        Path(self.repo, "app.py").write_text("print('hi')\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "init")

    def tearDown(self):
        for db in getattr(self, "_dbs", []):
            try:
                db.close()
            except Exception:
                pass
        self.tmp.cleanup()

    def rw_at(self, project, *args, stdin=None):
        old, buf = sys.stdin, io.StringIO()
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(buf):
                code = rewind.main(["--project", project, *args])
        finally:
            sys.stdin = old
        return code, buf.getvalue()

    def rw(self, *args, stdin=None):
        return self.rw_at(self.repo, *args, stdin=stdin)

    def record(self, cmd, code, output=""):
        return self.rw("record", "--command", cmd, "--exit", str(code), stdin=output)[1]

    def state(self):
        return json.loads(self.rw("state")[1].strip().splitlines()[-1])

    def db(self):
        import sqlite3
        db = sqlite3.connect(next(self.home.glob("projects/*/rewind.db")))
        db.row_factory = sqlite3.Row
        self._dbs = getattr(self, "_dbs", []) + [db]
        return db

    def run_cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, "-I", SCRIPT, "--project", self.repo, *args], input=stdin,
                              capture_output=True, text=True, env=os.environ)
