"""Self-reported evidence basis for a review finding."""

from __future__ import annotations

from enum import StrEnum, auto


class EvidenceStyle(StrEnum):
    """How a finding was arrived at, as reported by the model (#1925).

    Recorded and displayed only: v1 deliberately does no suppression or
    down-ranking on this field. Its one behavioral effect is the verify-first
    line added to prompts for speculative findings.

    Attributes:
        DIFF_LOCAL: Established from the diff hunk alone.
        CROSS_FILE: Established by tracing code outside the diff hunk.
        SPECULATIVE: Inferred rather than verified against the code.
    """

    DIFF_LOCAL = auto()
    CROSS_FILE = auto()
    SPECULATIVE = auto()
