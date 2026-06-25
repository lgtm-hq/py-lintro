"""Review finding from AI diff review."""

from __future__ import annotations

from dataclasses import dataclass, field


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

    severity: str
    category: str
    file: str
    line: int
    title: str
    description: str
    cause: str
    fix: str
    confidence: str
    checklist_ids: tuple[int, ...] = field(default_factory=tuple)
