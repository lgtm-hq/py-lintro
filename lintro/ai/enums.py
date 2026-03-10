"""AI-specific enumerations for confidence levels and risk classifications."""

from __future__ import annotations

from enum import StrEnum, auto

from lintro.enums.hyphenated_str_enum import HyphenatedStrEnum


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


class RiskLevel(HyphenatedStrEnum):
    """Risk classification for AI fix suggestions."""

    SAFE_STYLE = auto()
    BEHAVIORAL_RISK = auto()

    def to_severity_label(self, *, sarif: bool = False) -> str:
        """Map risk level to a severity label.

        Args:
            sarif: When True, return ``"note"`` for safe-style fixes
                (SARIF format). When False, return ``"notice"``
                (GitHub Actions annotation format).

        Returns:
            One of ``"warning"``, ``"note"``, or ``"notice"``.
        """
        if self == RiskLevel.SAFE_STYLE:
            return "note" if sarif else "notice"
        return "warning"
