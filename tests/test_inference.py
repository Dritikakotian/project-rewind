from pathlib import Path

from helpers import Base, git


class InferenceTrustTests(Base):
    """Every inferred memory says how sure Rewind is and why; the user can correct it."""

    def test_auto_failed_attempt_carries_evidence_and_confidence(self):
        self.record("flutter run", 1, "Error: build failed at step 1\n")
        Path(self.repo, "app.py").write_text("print('changed to fix')\n")
        self.rw("capture")
        self.record("flutter run", 1, "Error: build failed at step 2\n")  # no sleeps: ordering is by event, not clock
        dead = [d for d in self.state()["deadends"] if d["auto"]]
        self.assertEqual(len(dead), 1)
        self.assertGreaterEqual(dead[0]["confidence"], 0.7)
        self.assertTrue(any("app.py" in e for e in dead[0]["evidence"]))
        self.assertTrue(any("again" in e for e in dead[0]["evidence"]))

    def test_edit_before_the_first_error_is_not_blamed(self):
        Path(self.repo, "app.py").write_text("print('edited first')\n")
        self.rw("capture")
        self.record("flutter run", 1, "Error: build failed at step 1\n")
        self.record("flutter run", 1, "Error: build failed at step 2\n")  # nothing edited in between
        self.assertEqual([d for d in self.state()["deadends"] if d["auto"]], [])

    def test_branch_task_explains_itself(self):
        git(self.repo, "checkout", "-q", "-b", "feature/login-timeout")
        self.rw("capture")
        Path(self.repo, "app.py").write_text("print('x')\n")
        self.rw("capture")
        task = self.state()["mission"]
        self.assertTrue(task["auto"])
        self.assertIn("feature/login-timeout", task["evidence"][0])
        self.assertGreaterEqual(task["confidence"], 0.8)

    def test_comment_and_commit_inference_cite_their_source(self):
        self.rw("capture")
        Path(self.repo, "app.py").write_text("# NOTE: polling because websockets drop on mobile\nprint('x')\n")
        self.rw("capture")
        git(self.repo, "commit", "-qam", "Switch to polling", "-m", "Websockets kept dropping on mobile")
        self.rw("capture")
        by = {d["label"]: d for d in self.state()["decisions"]}
        note = next(v for k, v in by.items() if "websockets drop" in k)
        self.assertIn("NOTE comment in app.py", note["evidence"])
        commit = by["Switch to polling"]
        self.assertGreater(commit["confidence"], note["confidence"] - 0.2)
        self.assertTrue(any("message body" in e for e in commit["evidence"]))

    def test_inferred_pause_lists_the_facts_it_is_based_on(self):
        self.record("flutter run", 1, "Error: build failed\n")
        st = self.state()["stopped"]
        self.assertTrue(st["auto"])
        self.assertTrue(any(e.startswith("open error") for e in st["evidence"]))
        self.assertTrue(any(e.startswith("last command") for e in st["evidence"]))

    def test_dismissing_a_wrong_inference_hides_it_and_it_never_returns(self):
        Path(self.repo, "app.py").write_text("print('x')\n")
        self.rw("capture")
        task = self.state()["mission"]
        self.assertTrue(task["auto"])
        self.assertEqual(self.rw("dismiss", str(task["id"]))[0], 0)
        self.assertIsNone(self.state()["mission"])
        Path(self.repo, "app.py").write_text("print('y')\n")
        self.rw("capture")
        self.assertIsNone(self.state()["mission"])  # not re-inferred

    def test_confirming_raises_confidence_to_certain(self):
        self.record("flutter run", 1, "Error: build failed at step 1\n")
        Path(self.repo, "app.py").write_text("print('z')\n")
        self.rw("capture")
        self.record("flutter run", 1, "Error: build failed at step 2\n")
        dead = next(d for d in self.state()["deadends"] if d["auto"])
        self.rw("confirm", str(dead["id"]))
        dead = next(d for d in self.state()["deadends"] if d["id"] == dead["id"])
        self.assertEqual(dead["confidence"], 1.0)
        self.assertTrue(dead["confirmed"])

    def test_unknown_ids_are_reported(self):
        self.assertEqual(self.rw("dismiss", "99999")[0], 1)
        self.assertEqual(self.rw("confirm", "99999")[0], 1)

    def test_mission_reports_blockers_and_next_step(self):
        self.rw("task", "ship the login fix")
        self.assertEqual(self.state()["mission"]["status"], "in_progress")
        self.record("flutter run", 1, "Error: build failed\n")
        m = self.state()["mission"]
        self.assertEqual(m["status"], "blocked")
        self.assertIn("1 open error", m["status_text"])
        self.assertIn("fix:", m["next"])

    def test_pause_is_inferred_again_after_new_activity_even_in_the_same_second(self):
        self.rw("pause", "--hypothesis", "h", "--testing", "t", "--result", "r", "--next", "n")
        self.assertEqual(self.state()["stopped"]["source"], "saved")
        self.record("flutter run", 1, "Error: boom\n")  # immediately after: event order, not seconds
        self.assertEqual(self.state()["stopped"]["source"], "auto")

    def test_dismissed_errors_do_not_block_the_mission(self):
        self.rw("task", "x")
        self.record("flutter run", 1, "Error: boom\n")
        err = self.state()["errors"][0]
        self.rw("dismiss", str(err["id"]))
        self.assertEqual(self.state()["mission"]["status"], "in_progress")
