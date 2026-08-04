"""Lifecycle status for a tracked review finding."""

from __future__ import annotations

from enum import StrEnum, auto


class FindingStatus(StrEnum):
    """Persisted lifecycle status of a finding in the review state blob.

    Attributes:
        OPEN: The finding was reported by the most recent review round.
        RESOLVED: The finding disappeared in a later round and is considered
            addressed as of ``resolved_in``.
    """

    OPEN = auto()
    RESOLVED = auto()
