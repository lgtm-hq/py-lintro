"""Review-mode enum for the AI-powered ``idiom-review`` tool.

Kept in :mod:`lintro.enums` rather than beside the engine so the tool
definition can name its default mode without importing
:mod:`lintro.tools.idiom_review.engine` — and therefore without dragging the
whole :mod:`lintro.ai` stack into plugin discovery on ``chk``-only
invocations (#1305 / #1308).
"""

from __future__ import annotations

from enum import StrEnum


class IdiomReviewMode(StrEnum):
    """Which review modes the tool should run.

    Attributes:
        PER_FILE: Flag idiomatic misses within a single file.
        DUPLICATION: Flag the same logic reimplemented across files.
        BOTH: Run both modes in one pass.
    """

    PER_FILE = "per-file"
    DUPLICATION = "duplication"
    BOTH = "both"
