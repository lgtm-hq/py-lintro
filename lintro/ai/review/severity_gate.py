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
  ``contract-drift``, ``code-smell`` — must claim diff-local evidence; one
  whose ``evidence_style`` is ``cross_file``, ``speculative`` or unstated is
  moved to P3 (#2723), so "changes requested" means a shown defect. The two
  gates chain, so an inflated P1 in those categories ends at P3 like the
  honest P2 would. Categories that name incorrect behaviour (logic bugs,
  silent failures, security, integration, breaking changes) are never gated
  here: a cross-file trace is a legitimate way to show those.

A gate-lowered finding is never dropped downstream: the sensitivity filter
keeps every downgraded finding reportable, so the downgrade note always has
the finding it describes.

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
    "apply_severity_gates",
    "count_cross_chunk_contradictions",
    "count_downgrades",
    "count_downgrades_by_reason",
    "count_gate_firings",
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
        True when the P2 evidence gate applies. Questions are never gated. A
        P2 the P1 gate just produced *is* gated: an unevidenced test-gap
        claim inflated to P1 must land where the honest P2 lands, or the
        over-claim the gates exist to correct would be its own bypass.
    """
    if finding.is_question:
        return False
    return (
        finding.severity is Severity.P2
        and finding.category in P2_EVIDENCE_GATED_CATEGORIES
        and not finding.is_evidenced
    )


def apply_p2_evidence_gate(
    *,
    findings: Sequence[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Downgrade unevidenced test-gap, contract-drift and code-smell P2s (#2723).

    The gate fails closed: only an explicit ``diff_local`` claim counts as
    evidence, read through ``ReviewFinding.is_evidenced`` (the parser records
    the label as the model wrote it; the ``evidence_style`` fallback to
    ``diff_local`` serves display and the convergence score only).

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
            "Downgrading P2 finding {title!r} to P3: {reason} ({category}, "
            "claimed {claimed}).",
            title=finding.title,
            reason=P2_DOWNGRADE_REASON,
            category=finding.category,
            claimed=(
                str(finding.evidence_style)
                if finding.evidence_claimed is None
                else ("diff_local" if finding.evidence_claimed else "no diff_local")
            ),
        )
        chained = (
            finding.severity_downgrade_reason
            is SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO
        )
        gated.append(
            replace(
                finding,
                severity=Severity.P3,
                severity_downgraded=True,
                severity_downgrade_reason=(
                    SeverityDowngradeReason.P1_THEN_P2_UNEVIDENCED
                    if chained
                    else SeverityDowngradeReason.P2_UNEVIDENCED
                ),
            ),
        )
    return tuple(gated)


def apply_severity_gates(
    *,
    findings: Sequence[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Run the P1 gate then the P2 gate, in that order.

    One entry point so every caller — the per-chunk parser for custom
    agents and the depth-3 sweep, and the round-level finalizer for the
    built-in review after the verification pass (#2728) — chains the gates
    the same way.

    Args:
        findings: Findings to gate, in order.

    Returns:
        The gated findings in the same order.
    """
    return apply_p2_evidence_gate(findings=apply_p1_evidence_gate(findings=findings))


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
    count keeps its meaning; the P2 gate has its own count (#2723). A
    finding both gates moved is counted here too.

    Args:
        findings: Findings to count over.

    Returns:
        Number of findings the P1 gate moved, chained ones included.
    """
    return count_gate_firings(findings=findings)[0]


def count_downgrades_by_reason(
    *,
    findings: Iterable[ReviewFinding],
) -> dict[SeverityDowngradeReason, int]:
    """Count the gate-driven downgrades per recorded reason.

    Args:
        findings: Findings to count over.

    Returns:
        A count for every reason, zero included, in enum order. A record
        written before reasons existed (#2723) counts as the P1 gate, the
        one that existed then.
    """
    counts = dict.fromkeys(SeverityDowngradeReason, 0)
    for finding in downgraded_findings(findings=findings):
        reason = finding.severity_downgrade_reason
        if reason is None:
            reason = SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO
        counts[reason] += 1
    return counts


def count_gate_firings(*, findings: Iterable[ReviewFinding]) -> tuple[int, int]:
    """Count how many findings each gate moved, crediting a chain to both.

    Args:
        findings: Findings to count over.

    Returns:
        ``(p1_gate, p2_gate)``: findings the P1 gate moved (P1 → P2) and
        findings the P2 gate moved (P2 → P3); a finding both gates moved is
        counted in each.
    """
    by_reason = count_downgrades_by_reason(findings=findings)
    p1 = sum(count for reason, count in by_reason.items() if reason.p1_gate_fired)
    p2 = sum(count for reason, count in by_reason.items() if reason.p2_gate_fired)
    return p1, p2


def describe_downgrades(*, findings: Iterable[ReviewFinding]) -> str:
    """Build the one-line downgrade notice surfaces render.

    Args:
        findings: Findings to summarize.

    Returns:
        A line such as ``"1 finding downgraded to P2: no failure mechanism
        given; 2 findings downgraded to P3: no diff-local evidence …"``, or
        an empty string when nothing was downgraded.
    """
    p1, p2 = count_gate_firings(findings=findings)
    clauses: list[str] = []
    for count, target, wording in (
        (p1, Severity.P2, P1_DOWNGRADE_REASON),
        (p2, Severity.P3, P2_DOWNGRADE_REASON),
    ):
        if not count:
            continue
        noun = "finding" if count == 1 else "findings"
        clauses.append(f"{count} {noun} downgraded to {target}: {wording}")
    return "; ".join(clauses)
