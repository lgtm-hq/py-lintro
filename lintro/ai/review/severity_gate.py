"""Mechanical severity gates for review findings (#1925, #2265, #2723).

Severity inflation is the norm for AI reviewers — in the corpus behind epic
#1905 one bot marked 92% of its findings P1. Under a verdict derived from open
severities (any P1 -> Blocked) an uncalibrated P1 makes every PR read blocked,
and a verdict nobody believes is worse than no verdict at all.

The gates are deliberately mechanical rather than judgement calls, and each
records its rewrite on the finding so the downgrade is visible on the surfaces
instead of being an invisible edit of the model's output. Nothing is dropped.

* A P1 must carry a concrete ``failure_scenario``; one that does not is moved
  to P2 (#1925).
* A P2 in a category that flips the verdict on inference alone — ``test-gap``,
  ``contract-drift``, ``code-smell`` — must carry diff-local evidence; one whose
  ``evidence_style`` is ``cross_file`` or ``speculative`` is moved to P3
  (#2723), so "changes requested" means a shown defect. Categories that name
  incorrect behaviour (logic bugs, silent failures, security, integration,
  breaking changes) are never gated here: a cross-file trace is a legitimate
  way to show those.

The cross-chunk guard (#2265) is the third gate and shares that posture; it
lives in :mod:`lintro.ai.review.cross_chunk_gate` and is re-exported here so
every gate stays reachable from one import.
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
from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.severity_downgrade_reason import SeverityDowngradeReason
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.enums.review_category import ReviewCategory

__all__ = [
    "CROSS_CHUNK_DOWNGRADE_REASON",
    "P1_DOWNGRADE_REASON",
    "P2_DOWNGRADE_REASON",
    "P2_EVIDENCE_GATED_CATEGORIES",
    "UNCHANGED_CLAIM_PHRASES",
    "apply_cross_chunk_guard",
    "apply_p1_evidence_gate",
    "apply_p2_evidence_gate",
    "count_cross_chunk_contradictions",
    "count_downgrades",
    "count_downgrades_by_reason",
    "cross_chunk_contradictions",
    "describe_cross_chunk_contradictions",
    "describe_downgrades",
    "downgraded_findings",
]

#: Reason shown wherever a gate-driven downgrade is surfaced. Kept as one
#: constant so the sticky (#1909), the per-review comment (#1910), and the log
#: line can never drift into describing the rule differently.
P1_DOWNGRADE_REASON = "no failure mechanism given"

#: The same, for the P2 evidence gate (#2723).
P2_DOWNGRADE_REASON = (
    "no diff-local evidence for a test-gap, contract-drift or code-smell claim"
)

#: Categories whose P2 needs diff-local evidence (#2723). A test gap or a
#: contract drift the model inferred from code it traced elsewhere, or did not
#: verify at all, is a P3 until the diff itself shows it.
P2_EVIDENCE_GATED_CATEGORIES: frozenset[str] = frozenset(
    {
        str(ReviewCategory.TEST_GAP),
        str(ReviewCategory.CONTRACT_DRIFT),
        str(ReviewCategory.CODE_SMELL),
    },
)


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
                severity_downgrade_reason=SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO,
            ),
        )
    return tuple(gated)


def _needs_p2_downgrade(*, finding: ReviewFinding) -> bool:
    """Return True when a P2 in a gated category lacks diff-local evidence.

    Args:
        finding: Finding to test.

    Returns:
        True when the P2 evidence gate applies. Questions are never gated,
        and a P2 the P1 gate just produced is not gated again: its severity
        was already the gate's choice, not the model's claim.
    """
    if finding.is_question or finding.severity_downgraded:
        return False
    return (
        finding.severity is Severity.P2
        and finding.category in P2_EVIDENCE_GATED_CATEGORIES
        and finding.evidence_style is not EvidenceStyle.DIFF_LOCAL
    )


def apply_p2_evidence_gate(
    *,
    findings: Sequence[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Downgrade unevidenced test-gap, contract-drift and code-smell P2s (#2723).

    Args:
        findings: Findings after the P1 gate, in payload order.

    Returns:
        The same findings in the same order, with gated P2s rewritten to P3
        and marked via ``severity_downgraded`` and its reason.
    """
    gated: list[ReviewFinding] = []
    for finding in findings:
        if not _needs_p2_downgrade(finding=finding):
            gated.append(finding)
            continue
        logger.info(
            "Downgrading P2 finding {title!r} to P3: {reason} ({category}, {style}).",
            title=finding.title,
            reason=P2_DOWNGRADE_REASON,
            category=finding.category,
            style=finding.evidence_style,
        )
        gated.append(
            replace(
                finding,
                severity=Severity.P3,
                severity_downgraded=True,
                severity_downgrade_reason=SeverityDowngradeReason.P2_UNEVIDENCED,
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
    """Count the findings the P1 evidence gate downgraded.

    Kept to the P1 gate so the run record's long-standing ``downgraded``
    count keeps its meaning; the P2 gate has its own count (#2723).

    Args:
        findings: Findings to count over.

    Returns:
        Number of findings moved from P1 to P2.
    """
    return count_downgrades_by_reason(findings=findings)[
        SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO
    ]


def count_downgrades_by_reason(
    *,
    findings: Iterable[ReviewFinding],
) -> dict[SeverityDowngradeReason, int]:
    """Count the gate-driven downgrades per reason.

    Args:
        findings: Findings to count over.

    Returns:
        A count for every reason, zero included, in enum order.
    """
    counts = dict.fromkeys(SeverityDowngradeReason, 0)
    for finding in downgraded_findings(findings=findings):
        reason = finding.severity_downgrade_reason
        if reason is None:
            # A record written before reasons existed (#2723) can only have
            # come from the P1 gate, the one that existed then.
            reason = SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO
        counts[reason] += 1
    return counts


def describe_downgrades(*, findings: Iterable[ReviewFinding]) -> str:
    """Build the one-line downgrade notice surfaces render.

    Args:
        findings: Findings to summarize.

    Returns:
        A line such as ``"1 finding downgraded to P2: no failure mechanism
        given; 2 findings downgraded to P3: no diff-local evidence …"``, or
        an empty string when nothing was downgraded.
    """
    counts = count_downgrades_by_reason(findings=findings)
    clauses: list[str] = []
    for reason, target, wording in (
        (
            SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO,
            Severity.P2,
            P1_DOWNGRADE_REASON,
        ),
        (SeverityDowngradeReason.P2_UNEVIDENCED, Severity.P3, P2_DOWNGRADE_REASON),
    ):
        count = counts[reason]
        if not count:
            continue
        noun = "finding" if count == 1 else "findings"
        clauses.append(f"{count} {noun} downgraded to {target}: {wording}")
    return "; ".join(clauses)
