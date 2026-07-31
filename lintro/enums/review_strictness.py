"""Review strictness / sensitivity presets."""

from __future__ import annotations

from enum import auto

from lintro.enums.hyphenated_str_enum import HyphenatedStrEnum

__all__ = ["ReviewStrictness"]


class ReviewStrictness(HyphenatedStrEnum):
    """How aggressively lintro review surfaces non-blocking findings.

    * **focused** — merge blockers and behavioral issues; skip doc-only P3 nits.
    * **balanced** — default; report checklist yes answers as findings.
    * **thorough** — balanced plus explicit hunt for migration notes and doc
      drift (prompt-only; does not change chunking).
    """

    FOCUSED = auto()
    BALANCED = auto()
    THOROUGH = auto()
