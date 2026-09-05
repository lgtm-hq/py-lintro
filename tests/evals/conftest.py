"""Pytest configuration for the offline eval harness tests.

The harness lives in ``evals/review-efficacy/`` and is deliberately not a
packaged module, so its root is put on the import path here rather than being
installed. This runs before the test modules in this directory import
``review_matrix``.
"""

from __future__ import annotations

import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "review-efficacy"
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))
