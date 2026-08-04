"""Descriptor for findings that could not be posted as inline comments."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = ["InlinePostFailure"]


@dataclass(frozen=True, slots=True)
class InlinePostFailure:
    """Findings whose inline review comments could not be posted.

    The sticky comment must never be a verdict without substance (#1909): when
    inline posting fails the affected findings have no other surface, so the
    sticky folds their full details back in and says why, until a later round
    posts them successfully.

    Attributes:
        reason: Short human-readable cause, for example
            ``"review API returned 422 - line not in diff"``.
        findings: Findings that failed to post inline, in presentation order.
    """

    reason: str = ""
    findings: tuple[ReviewFinding, ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        """Return how many findings failed to post inline."""
        return len(self.findings)

    @property
    def is_empty(self) -> bool:
        """Return True when nothing failed to post."""
        return not self.findings
