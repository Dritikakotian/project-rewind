#!/usr/bin/env python3
"""Project Rewind entry point. The implementation lives in the rewind_core package."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rewind_core.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
