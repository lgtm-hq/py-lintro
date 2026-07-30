"""Confidence level enumeration for AI fix suggestions."""

from __future__ import annotations

from enum import StrEnum, auto


class ConfidenceLevel(StrEnum):
    """Confidence level for AI fix suggestions."""

    HIGH = auto()
    MEDIUM = auto()
    LOW = auto()

    @property
    def numeric_order(self) -> int:
        """Return a numeric ordering value (3=high, 2=medium, 1=low)."""
        return _CONFIDENCE_NUMERIC[self]


_CONFIDENCE_NUMERIC = {
    ConfidenceLevel.HIGH: 3,
    ConfidenceLevel.MEDIUM: 2,
    ConfidenceLevel.LOW: 1,
}
