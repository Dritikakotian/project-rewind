from pathlib import Path

from helpers import Base


class HandoffTests(Base):
    def test_secrets_never_reach_a_handoff(self):
        # built in pieces on purpose: these are fake, but key-shaped literals trip secret scanners on push
        secrets = ["hunter2" + "hunter2", "AKIA" + "ABCDEFGHIJKLMNOP", "sk-" + "abcdefghijklmnopqrstuvwx",
                   "ghp_" + "abcdefghijklmnopqrstuvwxyz0123",
                   "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".abcdefghij"]
        self.rw("task", "wire up auth")
        for i, sec in enumerate(secrets):
            self.rw("fail", f"tried token={sec} variant {i}", "--why", f"password={sec}")
            self.rw("decide", f"use key {sec}", "--why", f"secret: {sec}")
        _, o = self.rw("handoff", "--budget", "4000")
        for sec in secrets:
            self.assertNotIn(sec, o)
        self.assertIn("REDACTED", o)

    def test_unrelated_memories_are_omitted(self):
        self.rw("task", "fix payment webhook retries")
        for i in range(8):
            self.rw("fail", f"unrelated gardening experiment number {i}", "--why", "plants died")
        self.rw("fail", "retry the payment webhook immediately", "--why", "duplicate charges")
        _, o = self.rw("handoff", "--focus", "payment webhook")
        self.assertIn("duplicate charges", o)
        self.assertIn("omitted", o)

    def test_semantic_focus_pulls_in_related_wording(self):
        self.rw("task", "speed work")
        for i in range(8):
            self.rw("fail", f"unrelated gardening experiment number {i}", "--why", "plants died")
        self.rw("fail", "memoize the slow query results", "--why", "stale data")
        _, o = self.rw("handoff", "--focus", "improve latency with a cache")
        self.assertIn("memoize the slow query", o)

    def test_token_budget_is_respected(self):
        for i in range(30):
            self.rw("decide", f"decision number {i} " + "word " * 30, "--why", "because " * 30)
        _, o = self.rw("handoff", "--budget", "300")
        self.assertLess(len(o) / 4, 330)

    def test_handoff_at_a_milestone_excludes_later_knowledge(self):
        self.rw("task", "early task")
        cid = self.rw("checkpoint", "early")[1].split("#")[1].strip()
        self.rw("decide", "a much later decision", "--why", "later")
        _, o = self.rw("handoff", "--at", cid)
        self.assertNotIn("much later decision", o)

    def test_context_file_is_kept_current(self):
        self.rw("task", "keep a file current")
        st = self.state()
        text = Path(st["context_file"]).read_text(encoding="utf-8")
        self.assertIn("keep a file current", text)
