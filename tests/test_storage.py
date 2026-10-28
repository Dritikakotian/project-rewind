import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from helpers import SCRIPT, Base


class StorageTests(Base):
    def test_corrupt_database_is_set_aside_not_lost(self):
        self.rw("task", "before corruption")
        db_file = next(self.home.glob("projects/*/rewind.db"))
        for ext in ("-wal", "-shm"):
            Path(str(db_file) + ext).unlink(missing_ok=True)
        db_file.write_bytes(b"this is not a sqlite database" * 50)
        code, _ = self.rw("task", "after corruption")
        self.assertEqual(code, 0)
        self.assertTrue(list(db_file.parent.glob("rewind.db.corrupt-*")))
        self.assertIn("after corruption", self.rw("task")[1])

    def test_parallel_editor_events_do_not_lock_each_other_out(self):
        self.rw("task", "concurrency")
        files = []
        for i in range(8):
            f = Path(self.repo, f"f{i}.py")
            f.write_text(f"x = {i}\n")
            files.append(str(f))

        def fire(i):
            args = ["capture", "--file", files[i]] if i % 2 == 0 else ["record", "--command", f"cmd{i}", "--exit", "0"]
            return subprocess.run([sys.executable, "-I", SCRIPT, "--project", self.repo, *args], input="",
                                  capture_output=True, text=True, env=os.environ)

        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(fire, range(8)))
        for r in results:
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("locked", r.stderr.lower())

    def test_simultaneous_identical_problems_create_one_memory(self):
        self.rw("task", "race")
        payload = '{"app.py": ["Undefined name foo", "Expected a semicolon"]}'

        def fire(_):
            return subprocess.run([sys.executable, "-I", SCRIPT, "--project", self.repo, "diag"], input=payload,
                                  capture_output=True, text=True, env=os.environ)

        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(fire, range(8)))
        for r in results:
            self.assertEqual(r.returncode, 0, r.stderr)
        n = self.db().execute("SELECT count(*) FROM nodes WHERE type='error'").fetchone()[0]
        self.assertEqual(n, 2)  # the unique key held; no duplicates, no crashes

    def test_gc_bounds_growth_but_keeps_knowledge(self):
        self.rw("task", "long running project")
        self.rw("decide", "keep me", "--why", "important")
        db = self.db()
        db.executemany("INSERT INTO events(ts,kind,summary,data) VALUES('2000-01-01T00:00:00Z','edit','old','{}')",
                       [() for _ in range(800)])
        db.executemany("INSERT INTO checkpoints(ts,kind,title,branch,head,task,dirty,patch,untracked) "
                       "VALUES('2000-01-01T00:00:00Z','manual','old','b','h',NULL,'[]',?,'{\"a\":\"b\"}')", [("x" * 1000,)] * 60)
        db.commit()
        db.close()
        o = self.rw("gc", "--keep-patches", "10", "--days", "30")[1]
        self.assertIn("payloads_dropped=", o)
        db = self.db()
        self.assertLessEqual(db.execute("SELECT count(*) FROM events WHERE summary='old'").fetchone()[0], 500)
        self.assertEqual(db.execute("SELECT count(*) FROM checkpoints WHERE patch!=''").fetchone()[0], 10)
        self.assertEqual(db.execute("SELECT count(*) FROM nodes WHERE label='keep me'").fetchone()[0], 1)

    @unittest.skipIf(os.name == "nt", "POSIX permissions only")
    def test_database_is_private_to_the_user(self):
        self.rw("task", "perms")
        mode = next(self.home.glob("projects/*/rewind.db")).stat().st_mode & 0o777
        self.assertEqual(mode & 0o077, 0)

    def test_encryption_flag_fails_closed_without_the_library(self):
        try:
            import cryptography  # noqa: F401
            self.skipTest("cryptography installed: covered by the round-trip test")
        except ImportError:
            pass
        os.environ["REWIND_ENCRYPT"] = "1"
        try:
            with self.assertRaises(RuntimeError):  # refuses to store plaintext when encryption was requested
                self.rw("task", "x")
        finally:
            os.environ.pop("REWIND_ENCRYPT", None)

    def test_encrypted_payloads_round_trip(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography not installed")
        os.environ["REWIND_ENCRYPT"] = "1"
        try:
            self.rw("capture")
            Path(self.repo, "app.py").write_text("print('secret-ish change')\n")
            self.rw("checkpoint", "encrypted")
            row = self.db().execute("SELECT patch FROM checkpoints ORDER BY id DESC LIMIT 1").fetchone()
            self.assertTrue(row["patch"].startswith("enc:"))
            self.assertNotIn("secret-ish", row["patch"])
            cid = self.rw("checkpoints", "-n", "1")[1].split()[0].lstrip("#")
            self.assertIn("tracked files in the saved patch", self.rw("resume", cid)[1])
        finally:
            os.environ.pop("REWIND_ENCRYPT", None)
