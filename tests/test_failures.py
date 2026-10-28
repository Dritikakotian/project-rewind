from pathlib import Path

from helpers import Base


class FailureMemoryTests(Base):
    def test_repeat_error_remembers_what_was_tried(self):
        self.rw("task", "fix login")
        self.assertIn("new error logged", self.record("flutter run", 1, "Error: build failed at step 1\n"))
        self.rw("fail", "increase timeout to 60s", "--why", "still hangs")
        o = self.record("flutter run", 1, "Error: build failed at step 2\n")
        self.assertIn("seen this error 2x", o)
        self.assertIn("increase timeout", o)

    def test_edits_then_same_error_is_an_automatic_failed_attempt(self):
        self.record("flutter run", 1, "Error: build failed at step 1\n")
        Path(self.repo, "app.py").write_text("print('changed to fix')\n")
        self.rw("capture")
        self.assertIn("auto-recorded failed attempt", self.record("flutter run", 1, "Error: build failed at step 2\n"))
        self.assertIn("same error persisted", self.rw("failed")[1])

    def test_passing_again_records_a_fix_and_links_it(self):
        self.record("flutter run", 1, "Error: build failed\n")
        Path(self.repo, "app.py").write_text("print('fixed')\n")
        self.rw("capture")
        self.assertIn("now passes", self.record("flutter run", 0, ""))
        self.assertIn("fix", [m["kind"] for m in self.state()["milestones"]])
        rels = {r["rel"] for r in self.db().execute("SELECT rel FROM edges")}
        self.assertIn("resolved-by", rels)

    def test_same_execution_id_is_counted_once(self):
        for _ in range(2):
            self.rw("record", "--command", "flutter --version", "--exit", "0", "--id", "exec-1", stdin="")
        self.assertEqual(self.rw("ask", "what did I do recently")[1].count("flutter --version"), 1)

    def test_back_to_back_runs_without_an_id_are_all_recorded(self):
        for _ in range(3):  # no timing assumptions: rapid identical runs are real runs
            self.record("flutter --version", 0, "")
        self.assertEqual(self.rw("ask", "what did I do recently")[1].count("flutter --version"), 3)

    def test_check_matches_meaning_not_just_words(self):
        self.rw("fail", "increase timeout to 60 seconds", "--why", "still hangs")
        code, o = self.rw("check", "bump the request timeout up to 60")
        self.assertEqual(code, 3, o)
        self.assertIn("already attempted", o)
        self.assertEqual(self.rw("check", "rename the settings screen widget")[0], 0)

    def test_editor_problems_are_remembered_and_cleared(self):
        import json
        self.rw("diag", stdin=json.dumps({"app.py": ["Undefined name 'foo'", "Expected ';'"]}))
        st = self.state()
        self.assertEqual(len(st["errors"]), 2)
        db = self.db()  # pretend the problems have been open for a while
        db.execute("UPDATE nodes SET data=json_set(data,'$.first_ts','2000-01-01T00:00:00Z') WHERE type='error'")
        db.commit()
        db.close()
        self.rw("diag", stdin="{}")
        st = self.state()
        self.assertEqual(st["errors"], [])
        self.assertIn("fix", [m["kind"] for m in st["milestones"]])
