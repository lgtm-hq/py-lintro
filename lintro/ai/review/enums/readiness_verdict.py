"""Readiness verdict levels for AI diff review."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["ReadinessVerdict"]


class ReadinessVerdict(StrEnum):
    """Merge-readiness verdict derived from open finding severities.

    The verdict is never supplied by the model: it is computed by lintro from
    the severities of the open findings (see
    :func:`lintro.ai.review.verdict.derive_readiness_verdict`). The model only
    writes the reasoning that explains it.

    Attributes:
        BLOCKED: At least one open P1 finding.
        CHANGES_REQUESTED: No open P1, at least one open P2.
        NITS_ONLY: No open P1 or P2, at least one open P3.
        READY: No open findings at all.
    """

    BLOCKED = auto()
    CHANGES_REQUESTED = auto()
    NITS_ONLY = auto()
    READY = auto()
