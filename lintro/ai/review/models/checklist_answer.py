"""Checklist answer from AI review output."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChecklistAnswer:
    """A yes/no answer for a checklist item in a review result.

    Attributes:
        id: Prompt checklist item id (1..N in the review pass).
        answer: ``yes`` or ``no``.
        evidence: Brief file:line or logic trace supporting the answer.
        question: Full checklist question text (populated for JSON output).
    """

    id: int
    answer: str
    evidence: str
    question: str = ""
