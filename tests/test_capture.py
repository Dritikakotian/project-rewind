from pathlib import Path

from helpers import Base, git


class CaptureTests(Base):
    def test_private_files_are_never_captured(self):
        self.rw("capture")
        Path(self.repo, "app.py").write_text("print('v2')\n")
        Path(self.repo, ".env").write_text("API_KEY=supersecretvalue\n")
        Path(self.repo, "server.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
        _, o = self.rw("capture")
        self.assertIn("edit", o)
        blob = str(self.state()) + (Path(self.state()["context_file"]).read_text(encoding="utf-8"))
        self.assertNotIn(".env", blob)
        self.assertNotIn("server.pem", blob)
        self.assertNotIn("supersecretvalue", blob)

    def test_rewindignore_excludes_user_paths(self):
        self.rw("ignore", "private_notes/")
        Path(self.repo, "private_notes").mkdir()
        Path(self.repo, "private_notes", "plan.txt").write_text("secret plan")
        Path(self.repo, "app.py").write_text("print('v2')\n")
        self.rw("capture")
        self.assertNotIn("private_notes", str(self.state()))

    def test_commits_and_branches_become_milestones(self):
        self.rw("capture")
        git(self.repo, "checkout", "-q", "-b", "feature/login-timeout")
        Path(self.repo, "app.py").write_text("print('v2')\n")
        git(self.repo, "commit", "-qam", "second")
        self.rw("capture")
        kinds = [m["kind"] for m in self.state()["milestones"]]
        self.assertIn("commit", kinds)
        self.assertIn("start", kinds)

    def test_task_and_notes_inferred_without_typing(self):
        git(self.repo, "checkout", "-q", "-b", "feature/login-timeout")
        self.rw("capture")
        Path(self.repo, "app.py").write_text("# TODO: handle retry when offline\nprint('x')\n"
                                             "# NOTE: using polling because websockets drop on mobile\n")
        self.rw("capture")
        _, o = self.rw("task")
        self.assertIn("Work on login timeout", o)
        self.assertIn("TODO: handle retry when offline", o)
        self.assertIn("websockets drop on mobile", self.rw("ask", "why decisions")[1])
        Path(self.repo, "app.py").write_text("print('x')\n")
        self.rw("capture")
        self.assertIn("[x] TODO: handle retry", self.rw("task")[1])

    def test_commit_message_with_reason_becomes_decision(self):
        self.rw("capture")
        Path(self.repo, "app.py").write_text("print('x')\n")
        git(self.repo, "commit", "-qam", "Switch to polling", "-m", "Websockets kept dropping on mobile networks")
        self.rw("capture")
        self.assertIn("Switch to polling", self.rw("ask", "why decisions")[1])

    def test_non_git_folder_capture_and_safe_restore(self):
        plain = str(Path(self.tmp.name) / "plain")
        Path(plain).mkdir()
        Path(plain, "a.dart").write_text("one\n")
        Path(plain, "build").mkdir()
        Path(plain, "build", "junk.txt").write_text("x")
        self.rw_at(plain, "capture")  # baseline only
        Path(plain, "a.dart").write_text("two\n")
        Path(plain, "b.dart").write_text("new file\n")
        self.assertIn("2 file edit", self.rw_at(plain, "capture")[1])
        self.rw_at(plain, "task", "dart work")
        self.rw_at(plain, "checkpoint", "snap")
        Path(plain, "b.dart").unlink()
        Path(plain, "a.dart").write_text("three\n")
        o = self.rw_at(plain, "show")[1]
        self.assertIn("Recently edited", o)
        self.assertNotIn("junk", o)
        cid = self.rw_at(plain, "checkpoints", "-n", "1")[1].split()[0].lstrip("#")
        o = self.rw_at(plain, "resume", cid, "--apply")[1]
        self.assertIn("restored b.dart", o)
        self.assertIn("a.dart.rewind-cp", o)
        self.assertEqual(Path(plain, "a.dart").read_text(), "three\n")  # never overwritten

    def test_save_events_capture_only_given_files(self):
        self.rw("capture")
        Path(self.repo, "app.py").write_text("print('v2')\n")
        Path(self.repo, "other.py").write_text("x = 1\n")
        _, o = self.rw("capture", "--file", str(Path(self.repo, "app.py")))
        self.assertIn("1 file edit", o)
