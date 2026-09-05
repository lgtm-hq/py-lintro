#!/usr/bin/env python3
"""Entry point for the cross-provider review agreement matrix (issue #2147).

Run from the repository root::

    uv run python evals/review-efficacy/run_matrix.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from review_matrix.cli import main

if __name__ == "__main__":
    sys.exit(main())
