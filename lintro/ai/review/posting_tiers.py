"""Severity tiers for posting review findings (lintro-ops #37, decision A).

Not every finding earns an inline thread. The posting policy (#2572) decides
by confidence and kind; this module adds the severity tier on top: P1 and P2
findings open inline threads, P3 nits are listed in the sticky comment under
a disclosure and open no thread. The tier is rendering only: a P3 is still a
tracked record, still counts toward the derived verdict, and still shows as
fixed when a later round stops reporting it.

The boundary lives here, in one constant, so the inline poster, the review
body header, the sticky renderer and the resume replay cannot disagree about
which severities have threads.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.posting_policy import inline_findings

if TYPE_CHECKING:
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "INLINE_SEVERITIES",
    "inline_tier_findings",
    "is_inline_tier",
    "split_records_by_tier",
]

#: Severities that open inline threads. Everything else is sticky-only.
INLINE_SEVERITIES: frozenset[Severity] = frozenset({Severity.P1, Severity.P2})


def is_inline_tier(*, severity: Severity) -> bool:
    """Return whether findings of ``severity`` open inline threads.

    Args:
        severity: Finding severity.

    Returns:
        True for the severities in :data:`INLINE_SEVERITIES`.
    """
    return severity in INLINE_SEVERITIES


def inline_tier_findings(
    *,
    findings: Iterable[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Select the findings that get an inline thread this round.

    Applies the posting policy first (``posted_inline``) and the severity
    tier second, in the order given.

    Args:
        findings: Findings to filter.

    Returns:
        The policy-selected findings whose severity is in the inline tier.
    """
    return tuple(
        finding
        for finding in inline_findings(findings=findings)
        if is_inline_tier(severity=finding.severity)
    )


def split_records_by_tier(
    *,
    records: Iterable[FindingRecord],
) -> tuple[list[FindingRecord], list[FindingRecord]]:
    """Split tracked records into the inline tier and the sticky-only tier.

    Args:
        records: Records in presentation order.

    Returns:
        ``(inline, sticky_only)``, each preserving the input order.
    """
    inline: list[FindingRecord] = []
    sticky_only: list[FindingRecord] = []
    for record in records:
        if is_inline_tier(severity=record.severity):
            inline.append(record)
        else:
            sticky_only.append(record)
    return inline, sticky_only
