"""Descriptor for findings that could not be posted as inline comments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lintro.ai.review.enums.inline_post_failure_kind import InlinePostFailureKind
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
        reason: Short human-readable cause, derived from :attr:`kind` by
            :func:`lintro.ai.review.github_render.format_inline_post_cause`.
        findings: Findings that failed to post inline, in presentation order.
        kind: Classified cause. Defaults to
            :attr:`~InlinePostFailureKind.LINE_MAPPING`, the only cause that
            needs no GitHub answer to observe: those findings anchor to no
            line in the diff and were never submitted.
        status: HTTP status GitHub answered the review POST with, or ``None``
            when nothing was submitted for these findings (#2266).
    """

    reason: str = ""
    findings: tuple[ReviewFinding, ...] = field(default_factory=tuple)
    kind: InlinePostFailureKind = InlinePostFailureKind.LINE_MAPPING
    status: int | None = None

    @property
    def count(self) -> int:
        """Return how many findings failed to post inline."""
        return len(self.findings)

    @property
    def is_empty(self) -> bool:
        """Return True when nothing failed to post."""
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        """Serialize the failure for machine consumers.

        ``status`` is omitted when unset so a payload from a round that
        submitted nothing does not claim an HTTP answer it never got.

        Returns:
            A JSON-serializable mapping naming the kind, the count and the
            rendered reason.
        """
        payload: dict[str, Any] = {
            "kind": self.kind.value,
            "count": self.count,
            "reason": self.reason,
        }
        if self.status is not None:
            payload["status"] = self.status
        return payload
