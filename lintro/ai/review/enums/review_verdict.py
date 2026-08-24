"""Readiness verdict derived from the currently open review findings."""

from __future__ import annotations

from enum import StrEnum, auto


class ReviewVerdict(StrEnum):
    """Merge-readiness verdict for a PR at the end of a review round.

    The verdict is derived in code from open-finding severities (never asked of
    the model) so the label can never disagree with the findings it summarizes.

    Attributes:
        BLOCKED: At least one open P1 finding.
        CHANGES_REQUESTED: No open P1 findings but at least one open P2.
        NITS_ONLY: Only open P3 findings remain.
        READY: No open findings remain and coverage-at-HEAD is complete.
        INCOMPLETE: Coverage-at-HEAD is below 100% of review-eligible files.
            Overrides the findings-based label so a partial round can never
            render clean (#2154).
    """

    BLOCKED = auto()
    CHANGES_REQUESTED = auto()
    NITS_ONLY = auto()
    READY = auto()
    INCOMPLETE = auto()
