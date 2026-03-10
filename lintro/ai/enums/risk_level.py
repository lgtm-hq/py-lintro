"""Risk level enumeration for AI fix suggestions."""

from __future__ import annotations

from enum import auto

from lintro.enums.hyphenated_str_enum import HyphenatedStrEnum


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
