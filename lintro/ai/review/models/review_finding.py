"""Review finding from AI diff review."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.models.finding_occurrence import FindingOccurrence

__all__ = ["ReviewFinding", "Severity"]


class Severity(StrEnum):
    """Canonical severity levels for review findings.

    Members carry the uppercase labels emitted in review output and used by
    the P1 exit gate. Explicit ``P*`` values are used (instead of
    ``auto()``) because the canonical labels are uppercase and are compared
    and displayed verbatim across the review pipeline.

    Attributes:
        P1: Blocking severity that fails the review exit gate.
        P2: Important but non-blocking severity.
        P3: Minor or informational severity.
    """

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """An actionable finding from AI diff review.

    Attributes:
        severity: Finding severity (P1, P2, or P3).
        category: Finding category label.
        file: Repository-relative file path.
        line: Line number in the file.
        title: Short finding title, always a single line. Titles render
            inline in tables and headings, so embedded newlines are collapsed
            at parse time and the constraint is stated in the output schema.
        description: What is wrong and why it matters.
        cause: Root cause explanation.
        fix: Concise fix suggestion.
        confidence: Model confidence (high, medium, low).
        checklist_ids: Prompt checklist ids linked to this finding.
        suggested_code: Exact replacement code for the flagged line(s) when a
            mechanical fix applies. Rendered as a GitHub ``suggestion`` block on
            inline comments so reviewers can commit it in one click. Empty when
            no concrete code replacement is available.
        source: Provenance of the finding. Empty for the built-in checklist
            review; the agent name for a user-defined review agent (issue
            #1245) so merged output attributes each finding to its origin.
        kind: Whether this entry is a defect claim or an open question
            (#1925). Questions never affect the derived verdict and never get
            a fix prompt.
        failure_scenario: Concrete mechanism by which the defect fails in
            production. Required for P1: a P1 reported without one is
            downgraded to P2 at parse time and flagged via
            ``severity_downgraded``.
        severity_downgraded: True when the P1 evidence gate downgraded this
            finding's reported severity. Surfaces render the downgrade rather
            than letting it happen silently.
        evidence_style: Self-reported basis for the finding. Recorded and
            displayed only; the sole behavioral effect is the verify-first
            line prompts add for speculative findings.
        occurrences: Every ``file:line`` at which this pattern occurs. Empty
            when the model reported none, in which case
            :attr:`all_occurrences` falls back to the finding's own location.
    """

    severity: Severity
    category: str
    file: str
    line: int
    title: str
    description: str
    cause: str
    fix: str
    confidence: str
    checklist_ids: tuple[int, ...] = field(default_factory=tuple)
    suggested_code: str = ""
    source: str = ""
    kind: FindingKind = FindingKind.FINDING
    failure_scenario: str = ""
    severity_downgraded: bool = False
    evidence_style: EvidenceStyle = EvidenceStyle.DIFF_LOCAL
    occurrences: tuple[FindingOccurrence, ...] = field(default_factory=tuple)

    @property
    def all_occurrences(self) -> tuple[FindingOccurrence, ...]:
        """Return every location of this pattern, never empty.

        A finding that reports no explicit occurrences still occurs once, at
        its own ``file:line``, so callers can treat every finding uniformly as
        a pattern with at least one occurrence.

        Returns:
            The reported occurrences, or a single occurrence built from the
            finding's own file and line.
        """
        return self.occurrences or (FindingOccurrence(file=self.file, line=self.line),)

    @property
    def is_question(self) -> bool:
        """Return True when this entry is a question rather than a finding."""
        return self.kind is FindingKind.QUESTION
