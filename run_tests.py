"""Run the whole suite: python run_tests.py [pattern]  (e.g. python run_tests.py test_resume)"""
import os
import sys
import unittest

root = os.path.dirname(os.path.abspath(__file__))
tests = os.path.join(root, "tests")
sys.path[:0] = [root, tests]

pattern = (sys.argv[1] if len(sys.argv) > 1 else "test_*") + ".py"
suite = unittest.defaultTestLoader.discover(tests, pattern=pattern, top_level_dir=tests)
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
