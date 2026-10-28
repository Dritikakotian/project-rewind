import unittest
from pathlib import Path

from helpers import Base
from rewind_core.semantic import best_matches, similarity


class SemanticTests(unittest.TestCase):
    def test_paraphrases_score_higher_than_unrelated_text(self):
        close = similarity("increase the request timeout to 60 seconds", "bump the call deadline up to 60")
        far = similarity("increase the request timeout to 60 seconds", "rename the settings screen widget")
        self.assertGreater(close, 0.45)
        self.assertLess(far, 0.2)
        self.assertGreater(close, far * 3)

    def test_identical_text_is_one_and_empty_is_zero(self):
        self.assertAlmostEqual(similarity("fix login", "fix login"), 1.0, places=5)
        self.assertEqual(similarity("", "fix login"), 0.0)

    def test_identifiers_are_split_into_words(self):
        # CamelCase and snake_case names carry their meaning in their parts
        self.assertGreater(similarity("the timeout did not help", "TimeoutError: gateway failed"), 0.2)
        self.assertGreater(similarity("api client", "api_client.py"), 0.5)

    def test_typos_still_match(self):
        self.assertGreater(similarity("database migration failed", "databse migraton faild"), 0.3)

    def test_best_matches_orders_and_filters(self):
        items = ["gardening notes", "raise the timeout", "database index"]
        hits = best_matches("increase timeout", items, lambda s: s)
        self.assertEqual(hits[0][1], "raise the timeout")
        self.assertNotIn("gardening notes", [h[1] for h in hits])


class SimplifyTests(Base):
    def test_flags_common_over_engineering(self):
        Path(self.repo, "m.py").write_text(
            "import os\n\ndef f(x):\n    if x > 1:\n        return True\n    else:\n        return False\n\n"
            "def g(a):\n    for i in range(len(a)):\n        pass\n    return a == True\n")
        o = self.rw("simplify", "m.py")[1]
        for tip in ("unused import", "True/False", "range(len"):
            self.assertIn(tip, o)

    def test_clean_file_gets_no_suggestions(self):
        Path(self.repo, "ok.py").write_text("def add(a, b):\n    return a + b\n")
        self.assertIn("no simplification", self.rw("simplify", "ok.py")[1])

    def test_private_files_are_refused(self):
        Path(self.repo, ".env").write_text("A=1\n")
        self.assertEqual(self.rw("simplify", ".env")[0], 1)

    def test_broken_python_does_not_crash(self):
        Path(self.repo, "bad.py").write_text("def (:\n")
        self.assertEqual(self.rw("simplify", "bad.py")[0], 0)
