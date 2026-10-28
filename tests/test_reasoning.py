from pathlib import Path

from helpers import Base


class ReasoningTests(Base):
    def test_decision_is_linked_to_the_dead_end_that_caused_it(self):
        self.rw("fail", "increase timeout to 60 seconds", "--why", "still hangs")
        self.rw("decide", "use retry with exponential backoff", "--why", "raising the timeout did not help")
        rels = {(r["rel"]) for r in self.db().execute("SELECT rel FROM edges")}
        self.assertIn("because", rels)
        o = self.rw("trace", "retry with backoff")[1]
        self.assertIn("-because->", o)
        self.assertIn("increase timeout", o)

    def test_trace_for_a_file_lists_its_history(self):
        self.rw("capture")
        Path(self.repo, "app.py").write_text("print('x')\n")
        self.rw("capture")
        self.rw("decide", "keep app.py tiny", "--why", "easier to test", "--file", "app.py")
        o = self.rw("trace", "app.py")[1]
        self.assertIn("keep app.py tiny", o)

    def test_trace_with_nothing_known_says_so(self):
        self.assertIn("Nothing recorded", self.rw("trace", "quantum flux")[1])

    def test_error_to_fix_chain_is_traceable(self):
        self.record("flutter run", 1, "Error: gradle build failed\n")
        self.rw("solved", "upgraded gradle plugin")
        o = self.rw("trace", "gradle build failed")[1]
        self.assertIn("resolved-by", o)

    def test_agent_setup_is_opt_in_and_idempotent(self):
        o = self.rw("agent-setup")[1]
        self.assertIn("rewind_context", o)
        self.assertFalse(Path(self.repo, "AGENTS.md").exists())  # printing never touches the project
        self.rw("agent-setup", "--target", "claude", "--write")
        self.rw("agent-setup", "--target", "claude", "--write")
        text = Path(self.repo, "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("rewind:start"), 1)
