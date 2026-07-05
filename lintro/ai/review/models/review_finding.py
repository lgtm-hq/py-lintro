"""Review finding from AI diff review."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

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
        title: Short finding title.
        description: What is wrong and why it matters.
        cause: Root cause explanation.
        fix: Concise fix suggestion.
        confidence: Model confidence (high, medium, low).
        checklist_ids: Prompt checklist ids linked to this finding.
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
