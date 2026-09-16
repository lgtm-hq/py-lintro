"""Helpers for resolving checklist display and linking findings to questions.

Chunks answer with findings only (lintro-ops milestone 0, decision A), so the
checklist is prompt guidance and the only rendered link is from a finding's
``checklist_ids`` back to the question text.
"""

from __future__ import annotations

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "build_prompt_question_map",
    "format_review_questions_markdown",
    "questions_for_finding",
    "resolve_checklist_display",
]


def resolve_checklist_display(
    *,
    cli_value: str | None,
    config_value: ChecklistDisplay,
) -> ChecklistDisplay:
    """Resolve effective checklist display mode from CLI and config.

    Args:
        cli_value: Optional ``--show-checklist`` value (``off``, ``linked``,
            or ``all``).
        config_value: Default from ``review.checklist_display``.

    Returns:
        Effective display mode.
    """
    if cli_value is not None:
        return ChecklistDisplay(cli_value.lower())
    return config_value


def build_prompt_question_map(
    *,
    items: list[ChecklistItem],
) -> dict[int, str]:
    """Map prompt checklist ids (1..N) to question text.

    Args:
        items: Selected checklist items in prompt order.

    Returns:
        Mapping from prompt id to question string.
    """
    return {prompt_id: item.question for prompt_id, item in enumerate(items, start=1)}


def questions_for_finding(
    *,
    finding: ReviewFinding,
    question_map: dict[int, str],
) -> tuple[str, ...]:
    """Return linked review question text for a finding.

    Args:
        finding: Review finding with optional checklist_ids.
        question_map: Prompt id to question mapping.

    Returns:
        Question strings in checklist_ids order, skipping unknown ids.
    """
    questions: list[str] = []
    for checklist_id in finding.checklist_ids:
        question = question_map.get(checklist_id, "").strip()
        if question:
            questions.append(question)
    return tuple(questions)


def format_review_questions_markdown(
    *,
    questions: tuple[str, ...],
) -> str:
    """Format linked review questions as markdown bullets.

    Args:
        questions: Question strings to render.

    Returns:
        Markdown block or empty string when no questions.
    """
    if not questions:
        return ""
    lines = ["", "**Review questions:**"]
    lines.extend(f"- {question}" for question in questions)
    return "\n".join(lines)
