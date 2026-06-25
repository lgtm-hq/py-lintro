"""Helpers for resolving and rendering checklist display."""

from __future__ import annotations

from dataclasses import replace

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult

__all__ = [
    "build_prompt_question_map",
    "cleared_answers",
    "enrich_review_result",
    "format_review_questions_markdown",
    "orphan_concerns",
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
        cli_value: Optional ``--show-checklist`` value (``linked`` or ``all``).
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
    return {
        prompt_id: item.question
        for prompt_id, item in enumerate(items, start=1)
    }


def enrich_review_result(
    *,
    result: ReviewResult,
    question_map: dict[int, str],
) -> ReviewResult:
    """Attach question text to checklist answers on a review result.

    Args:
        result: Raw review result from the orchestrator.
        question_map: Prompt id to question mapping.

    Returns:
        Review result with enriched checklist answers.
    """
    enriched = tuple(
        ChecklistAnswer(
            id=answer.id,
            answer=answer.answer,
            evidence=answer.evidence,
            question=question_map.get(answer.id, ""),
        )
        for answer in result.checklist
    )
    return replace(result, checklist=enriched)


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


def cleared_answers(
    *,
    answers: tuple[ChecklistAnswer, ...],
) -> tuple[ChecklistAnswer, ...]:
    """Return checklist answers marked as cleared (no concern).

    Args:
        answers: Enriched checklist answers.

    Returns:
        Answers where the model responded ``no``.
    """
    return tuple(
        answer for answer in answers if answer.answer.lower() == "no"
    )


def orphan_concerns(
    *,
    answers: tuple[ChecklistAnswer, ...],
    findings: tuple[ReviewFinding, ...],
) -> tuple[ChecklistAnswer, ...]:
    """Return yes answers not linked from any finding.

    Args:
        answers: Enriched checklist answers.
        findings: Review findings.

    Returns:
        Checklist yes answers whose prompt id is absent from all findings.
    """
    linked_ids = {
        checklist_id
        for finding in findings
        for checklist_id in finding.checklist_ids
    }
    return tuple(
        answer
        for answer in answers
        if answer.answer.lower() == "yes" and answer.id not in linked_ids
    )


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
