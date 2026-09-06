"""Severity tallies and the run-over-run delta derived from them.

Lintro reports what a run actually found — how many ERROR, WARNING and INFO
issues — and how that changed since the previous run in the same workspace.
Both values are exact and linear: one issue fixed always moves the delta by
one, whatever the size of the project.

This replaces the 0-100 health score deleted in issue #1739. That score had no
size normalisation, so the same number meant different things in different
repositories, and enabling more tools mechanically lowered it. Counts do not
have that defect, and a count delta is the only comparison the old score was
ever legitimately used for.

Note the inversion the renderer relies on: **fewer issues is better**, so a
negative delta is an improvement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SeverityCounts:
    """Tally of issues by normalized severity.

    Attributes:
        errors: Number of ERROR-severity issues.
        warnings: Number of WARNING-severity issues.
        info: Number of INFO-severity issues.
    """

    errors: int = 0
    warnings: int = 0
    info: int = 0

    @property
    def total(self) -> int:
        """Return the total number of issues across all severities.

        Returns:
            int: Sum of errors, warnings, and info issues.
        """
        return self.errors + self.warnings + self.info

    def to_dict(self) -> dict[str, int]:
        """Serialize the counts to a JSON-safe dictionary.

        Returns:
            dict[str, int]: Keys ``error``, ``warning``, ``info`` and
            ``total``.
        """
        return {
            "error": self.errors,
            "warning": self.warnings,
            "info": self.info,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeverityCounts:
        """Rebuild counts from a :meth:`to_dict` payload.

        Unknown keys are ignored, and missing, non-integer or negative values
        fall back to zero, so a hand-edited or older baseline file degrades to
        "no issues recorded" instead of raising or producing a delta against a
        count that cannot exist.

        Args:
            data: Mapping produced by :meth:`to_dict`.

        Returns:
            SeverityCounts: The parsed counts.
        """

        def _read(key: str) -> int:
            value = data.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                return 0
            return max(0, value)

        return cls(
            errors=_read("error"),
            warnings=_read("warning"),
            info=_read("info"),
        )


@dataclass(frozen=True)
class SeverityDelta:
    """Change in severity counts between two runs.

    Each field is ``current - previous``, so a **negative** value means fewer
    issues, which is an improvement.

    Attributes:
        errors: Change in ERROR-severity issues.
        warnings: Change in WARNING-severity issues.
        info: Change in INFO-severity issues.
    """

    errors: int = 0
    warnings: int = 0
    info: int = 0

    @property
    def total(self) -> int:
        """Return the change in the total issue count.

        Returns:
            int: Sum of the per-severity changes.
        """
        return self.errors + self.warnings + self.info

    @property
    def is_empty(self) -> bool:
        """Whether nothing changed between the two runs.

        Returns:
            bool: ``True`` when every per-severity change is zero.
        """
        return self.errors == 0 and self.warnings == 0 and self.info == 0

    def to_dict(self) -> dict[str, int]:
        """Serialize the delta to a JSON-safe dictionary.

        Returns:
            dict[str, int]: Keys ``error``, ``warning``, ``info`` and
            ``total``.
        """
        return {
            "error": self.errors,
            "warning": self.warnings,
            "info": self.info,
            "total": self.total,
        }

    @classmethod
    def between(
        cls,
        *,
        current: SeverityCounts,
        previous: SeverityCounts,
    ) -> SeverityDelta:
        """Compute the delta from ``previous`` to ``current``.

        Args:
            current: Counts for the run that just finished.
            previous: Counts recorded for the preceding run.

        Returns:
            SeverityDelta: Per-severity ``current - previous`` differences.
        """
        return cls(
            errors=current.errors - previous.errors,
            warnings=current.warnings - previous.warnings,
            info=current.info - previous.info,
        )
