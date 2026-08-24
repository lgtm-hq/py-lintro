"""Complete AI review result."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.file_assessment import FileAssessment
from lintro.ai.review.models.flagged_file import FlaggedFile
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_summary import ReviewSummary
from lintro.ai.review.models.verdict_reasoning import VerdictReasoning


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Complete result from an AI diff review run.

    Attributes:
        metadata: Run metadata (model, tokens, cost, etc.).
        summary: High-level review summary text. Equal to
            ``pr_summary.headline`` when a structured summary was returned.
        checklist: Checklist yes/no answers with evidence.
        findings: Actionable findings from the review.
        pr_summary: Structured PR summary (headline plus walkthrough bullets).
            ``None`` when the model returned only plain summary text.
        verdict_reasoning: Model-written explanation of the readiness verdict.
            ``None`` when the model omitted it.
        file_assessments: One-sentence overview per reviewed file. Empty when
            the model omitted them.
        coverage: Per-round coverage counters, or ``None`` before resume
            bookkeeping runs.
        coverage_records: File-level coverage map after this round.
        flagged_files: Guarded re-read requests for the next round.
        awaiting_paths: Eligible paths not yet covered at HEAD, with an
            optional flag reason in ``awaiting_reasons``.
        awaiting_reasons: Path to reviewer flag reason for awaiting files.
    """

    metadata: ReviewMetadata
    summary: str
    checklist: tuple[ChecklistAnswer, ...] = field(default_factory=tuple)
    findings: tuple[ReviewFinding, ...] = field(default_factory=tuple)
    pr_summary: ReviewSummary | None = None
    verdict_reasoning: VerdictReasoning | None = None
    file_assessments: tuple[FileAssessment, ...] = field(default_factory=tuple)
    coverage: CoverageCounts | None = None
    coverage_records: tuple[CoverageRecord, ...] = field(default_factory=tuple)
    flagged_files: tuple[FlaggedFile, ...] = field(default_factory=tuple)
    awaiting_paths: tuple[str, ...] = field(default_factory=tuple)
    awaiting_reasons: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def has_p1_findings(self) -> bool:
        """Return True when any P1 finding exists."""
        return any(finding.severity == Severity.P1 for finding in self.findings)

    @property
    def readiness_verdict(self) -> ReviewVerdict:
        """Return the merge-readiness verdict derived from this run's findings.

        The verdict is computed from finding severities, never taken from the
        model. See :mod:`lintro.ai.review.verdict` for the rubric.

        Returns:
            The derived readiness verdict.
        """
        from lintro.ai.review.verdict import (
            apply_coverage_gate,
            derive_readiness_verdict,
        )

        findings_verdict = derive_readiness_verdict(findings=self.findings)
        if self.coverage is None:
            return findings_verdict
        return apply_coverage_gate(
            findings_verdict=findings_verdict,
            coverage_complete=self.coverage.complete,
        )
