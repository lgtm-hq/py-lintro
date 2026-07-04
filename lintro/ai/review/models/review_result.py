"""Complete AI review result."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Complete result from an AI diff review run.

    Attributes:
        metadata: Run metadata (model, tokens, cost, etc.).
        summary: High-level review summary text.
        checklist: Checklist yes/no answers with evidence.
        findings: Actionable findings from the review.
    """

    metadata: ReviewMetadata
    summary: str
    checklist: tuple[ChecklistAnswer, ...] = field(default_factory=tuple)
    findings: tuple[ReviewFinding, ...] = field(default_factory=tuple)

    @property
    def has_p1_findings(self) -> bool:
        """Return True when any P1 finding exists."""
        return any(finding.severity == "P1" for finding in self.findings)
