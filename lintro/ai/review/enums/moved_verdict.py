"""What the moved-finding verification decided about one finding (#3466)."""

from __future__ import annotations

from enum import StrEnum, auto


class MovedVerdict(StrEnum):
    """The verifier's answer for one prior finding whose location was inferred.

    Attributes:
        FIXED: The provider said the original issue no longer exists anywhere
            in the changed files (``still_present: false``); the only answer
            that may resolve the finding.
        PRESENT: The provider said the issue still exists
            (``still_present: true``); the finding stays open, moved to the
            location it named when that location is inside the diff.
        UNKNOWN: No usable answer: ``still_present: unknown``, a provider
            error or timeout, an exhausted budget, an off-schema reply, or
            the check turned off. The finding is held, never resolved.
    """

    FIXED = auto()
    PRESENT = auto()
    UNKNOWN = auto()
