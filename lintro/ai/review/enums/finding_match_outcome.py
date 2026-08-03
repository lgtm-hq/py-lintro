"""Per-round transition outcomes produced by the cross-run finding matcher."""

from __future__ import annotations

from enum import StrEnum, auto


class FindingMatchOutcome(StrEnum):
    """Transition assigned to a finding when a review round is matched.

    Attributes:
        NEW: First round in which the finding was seen.
        CARRIED: Still open and previously seen; ``since_round`` is preserved.
        RESOLVED: Previously open, absent from this round, stamped with the
            head sha and round that resolved it.
        REGRESSED: Previously resolved and reported again in this round.
    """

    NEW = auto()
    CARRIED = auto()
    RESOLVED = auto()
    REGRESSED = auto()
