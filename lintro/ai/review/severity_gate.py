"""Mechanical severity gates for review findings (#1925, #2265).

Severity inflation is the norm for AI reviewers — in the corpus behind epic
#1905 one bot marked 92% of its findings P1. Under a verdict derived from open
severities (any P1 -> Blocked) an uncalibrated P1 makes every PR read blocked,
and a verdict nobody believes is worse than no verdict at all.

The gate is deliberately mechanical rather than a judgement call: a P1 must
carry a concrete ``failure_scenario``. One that does not is downgraded to P2 at
parse time and marked, so the downgrade is visible on the surfaces instead of
being an invisible edit of the model's output. Nothing is dropped, and no other
severity is touched.

The cross-chunk guard (#2265) is the second gate and shares that posture; it
lives in :mod:`lintro.ai.review.cross_chunk_gate` and is re-exported here so
both gates stay reachable from one import.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from loguru import logger

from lintro.ai.review.cross_chunk_gate import (
    CROSS_CHUNK_DOWNGRADE_REASON,
    UNCHANGED_CLAIM_PHRASES,
    apply_cross_chunk_guard,
    count_cross_chunk_contradictions,
    cross_chunk_contradictions,
    describe_cross_chunk_contradictions,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity

__all__ = [
    "CROSS_CHUNK_DOWNGRADE_REASON",
    "P1_DOWNGRADE_REASON",
    "UNCHANGED_CLAIM_PHRASES",
    "apply_cross_chunk_guard",
    "apply_p1_evidence_gate",
    "count_cross_chunk_contradictions",
    "count_downgrades",
    "cross_chunk_contradictions",
    "describe_cross_chunk_contradictions",
    "describe_downgrades",
    "downgraded_findings",
]

#: Reason shown wherever a gate-driven downgrade is surfaced. Kept as one
#: constant so the sticky (#1909), the per-review comment (#1910), and the log
#: line can never drift into describing the rule differently.
P1_DOWNGRADE_REASON = "no failure mechanism given"


def _needs_downgrade(*, finding: ReviewFinding) -> bool:
    """Return True when a finding is a P1 without a failure scenario.

    Args:
        finding: Finding to test.

    Returns:
        True when the P1 evidence gate applies to this finding. Questions are
        never gated: they carry no severity semantics to begin with.
    """
    if finding.is_question:
        return False
    return finding.severity is Severity.P1 and not finding.failure_scenario.strip()


def apply_p1_evidence_gate(
    *,
    findings: Sequence[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Downgrade P1 findings that report no concrete failure scenario.

    Args:
        findings: Findings as reported by the model, in payload order.

    Returns:
        The same findings in the same order, with ungated P1s rewritten to P2
        and marked via ``severity_downgraded``.
    """
    gated: list[ReviewFinding] = []
    for finding in findings:
        if not _needs_downgrade(finding=finding):
            gated.append(finding)
            continue
        logger.info(
            "Downgrading P1 finding {title!r} to P2: {reason}.",
            title=finding.title,
            reason=P1_DOWNGRADE_REASON,
        )
        gated.append(
            replace(
                finding,
                severity=Severity.P2,
                severity_downgraded=True,
            ),
        )
    return tuple(gated)


def downgraded_findings(
    *,
    findings: Iterable[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Select the findings the evidence gate downgraded.

    Args:
        findings: Findings to filter.

    Returns:
        The downgraded findings, in the order given.
    """
    return tuple(finding for finding in findings if finding.severity_downgraded)


def count_downgrades(*, findings: Iterable[ReviewFinding]) -> int:
    """Count the findings the evidence gate downgraded.

    Args:
        findings: Findings to count over.

    Returns:
        Number of downgraded findings.
    """
    return len(downgraded_findings(findings=findings))


def describe_downgrades(*, findings: Iterable[ReviewFinding]) -> str:
    """Build the one-line downgrade notice surfaces render.

    Args:
        findings: Findings to summarize.

    Returns:
        A line such as ``"1 finding downgraded to P2: no failure mechanism
        given"``, or an empty string when nothing was downgraded.
    """
    count = count_downgrades(findings=findings)
    if not count:
        return ""
    noun = "finding" if count == 1 else "findings"
    return f"{count} {noun} downgraded to {Severity.P2}: {P1_DOWNGRADE_REASON}"
