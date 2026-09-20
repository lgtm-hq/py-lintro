"""Lifecycle status for a tracked review finding."""

from __future__ import annotations

from enum import StrEnum, auto


class FindingStatus(StrEnum):
    """Persisted lifecycle status of a finding in the review state blob.

    Attributes:
        OPEN: The finding was reported by the most recent review round.
        RESOLVED: The finding disappeared in a later round and is considered
            addressed as of ``resolved_in``.
        REBASELINED: The finding was open in a state written before schema
            v4 (#2723). Its fingerprint may not be reproducible by the current
            parser, so it is archived: kept for history, never matched against,
            never counted as open or as fixed. The round after the upgrade
            reports the still-present findings as new once and the sticky's
            fine print names how many records were re-baselined.
    """

    OPEN = auto()
    RESOLVED = auto()
    REBASELINED = auto()
