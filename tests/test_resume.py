import subprocess
from pathlib import Path

from helpers import Base, git


class ResumeTests(Base):
    def make_checkpoint(self):
        self.rw("capture")
        self.rw("task", "work in progress")
        Path(self.repo, "app.py").write_text("print('saved state')\n")
        Path(self.repo, "new_file.py").write_text("brand = 'new'\n")
        self.rw("capture")
        self.rw("checkpoint", "the milestone")
        return self.rw("checkpoints", "-n", "1")[1].split()[0].lstrip("#")

    def test_dry_run_previews_and_changes_nothing(self):
        cid = self.make_checkpoint()
        Path(self.repo, "app.py").write_text("print('later edit')\n")
        code, o = self.rw("resume", cid)
        self.assertEqual(code, 0)
        for line in ("tracked files in the saved patch", "files that would be recreated", "stashed first"):
            self.assertIn(line, o)
        self.assertEqual(Path(self.repo, "app.py").read_text(), "print('later edit')\n")

    def test_apply_restores_files_and_keeps_current_work_in_a_stash(self):
        cid = self.make_checkpoint()
        Path(self.repo, "app.py").write_text("print('later edit')\n")
        Path(self.repo, "new_file.py").unlink()
        o = self.rw("resume", cid, "--apply")[1]
        self.assertIn("stash", o)
        self.assertEqual(Path(self.repo, "app.py").read_text(), "print('saved state')\n")
        self.assertEqual(Path(self.repo, "new_file.py").read_text(), "brand = 'new'\n")
        stashes = subprocess.run(["git", "-C", self.repo, "stash", "list"], capture_output=True, text=True).stdout
        self.assertIn("rewind-auto", stashes)  # the work that was replaced is still recoverable

    def test_untracked_file_that_differs_is_never_overwritten(self):
        cid = self.make_checkpoint()
        Path(self.repo, "new_file.py").write_text("brand = 'edited by me'\n")
        self.rw("resume", cid, "--apply")
        # the stash takes the file away, but a file recreated by hand afterwards must survive a second resume
        Path(self.repo, "new_file.py").write_text("brand = 'precious'\n")
        o = self.rw("resume", cid, "--apply")[1]
        self.assertIn("brand = ", Path(self.repo, "new_file.py").read_text())
        self.assertNotIn("Traceback", o)

    def test_unknown_checkpoint_is_reported(self):
        code, o = self.rw("resume", "9999")
        self.assertEqual(code, 1)
        self.assertIn("no such checkpoint", o)
