import json
from pathlib import Path

from helpers import Base


class GraphAndStateTests(Base):
    def seed(self):
        self.rw("task", "build graph")
        self.rw("decide", "use sqlite", "--why", "local first", "--file", "app.py")
        self.rw("fail", "use a cloud database", "--why", "must stay local")

    def test_state_has_everything_the_panel_needs(self):
        self.seed()
        st = self.state()
        for key in ("project", "task", "tasks", "stopped", "errors", "decisions", "deadends", "milestones", "context_file"):
            self.assertIn(key, st)
        self.assertEqual(st["task"]["label"], "build graph")
        self.assertEqual(st["decisions"][0]["why"], "local first")

    def test_graph_exports(self):
        self.seed()
        out_file = str(Path(self.tmp.name) / "g.html")
        self.rw("graph", "-o", out_file)
        html = Path(out_file).read_text(encoding="utf-8")
        self.assertIn("use sqlite", html)
        self.assertIn("Copy AI handoff", html)
        self.assertTrue(self.rw("graph", "--format", "mermaid")[1].startswith("graph LR"))
        data = json.loads(self.rw("graph", "--format", "json")[1])
        self.assertTrue(data["handoffs"])
        self.assertTrue(any(e["rel"] == "about" for e in data["edges"]))

    def test_graph_html_cannot_be_broken_by_content(self):
        self.rw("decide", "</script><script>alert(1)</script>", "--why", "x")
        out_file = str(Path(self.tmp.name) / "g.html")
        self.rw("graph", "-o", out_file)
        html = Path(out_file).read_text(encoding="utf-8")
        self.assertEqual(html.count("</script>"), 1)  # only the template's own closing tag

    def test_empty_project_renders(self):
        code, o = self.rw("state")
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(o.strip().splitlines()[-1])["task"])
        self.assertEqual(self.rw("graph", "--format", "mermaid")[0], 0)

    def test_away_report_lists_new_commits(self):
        self.rw("capture")
        Path(self.repo, "app.py").write_text("print('teammate')\n")
        Path(self.repo, "requirements.txt").write_text("requests\n")
        import subprocess
        subprocess.run(["git", "-C", self.repo, "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=Teammate", "commit", "-qm", "Merge pull request #7"],
                       check=True, capture_output=True)
        away = self.state()["away"]
        self.assertEqual(len(away["commits"]), 1)
        self.assertIn("requirements.txt", away["deps"])
        self.rw("dismiss-away")
        self.assertIsNone(self.state()["away"])
