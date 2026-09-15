"""Tests for checklist display helpers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.checklist_display import (
    build_prompt_question_map,
    questions_for_finding,
    resolve_checklist_display,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.review_finding import ReviewFinding, Severity


def test_build_prompt_question_map_uses_prompt_order() -> None:
    """Prompt ids follow enumerate order starting at 1."""
    items = [
        ChecklistItem(
            id=100,
            question="First question?",
            domains=(),
            languages=(),
            category=ReviewCategory.SECURITY,
            tier=1,
        ),
        ChecklistItem(
            id=200,
            question="Second question?",
            domains=(),
            languages=(),
            category=ReviewCategory.TEST_GAP,
            tier=1,
        ),
    ]

    question_map = build_prompt_question_map(items=items)

    assert_that(question_map).is_equal_to(
        {1: "First question?", 2: "Second question?"},
    )


def test_resolve_checklist_display_prefers_cli() -> None:
    """CLI flag overrides config default."""
    resolved = resolve_checklist_display(
        cli_value="all",
        config_value=ChecklistDisplay.OFF,
    )

    assert_that(resolved).is_equal_to(ChecklistDisplay.ALL)


def test_questions_for_finding_returns_linked_prompt_questions() -> None:
    """Finding checklist_ids resolve to question strings in order."""
    finding = ReviewFinding(
        severity=Severity.P1,
        category="security",
        file="a.py",
        line=1,
        title="Issue",
        description="desc",
        cause="cause",
        fix="fix",
        confidence="high",
        checklist_ids=(2, 99),
    )

    questions = questions_for_finding(
        finding=finding,
        question_map={1: "One", 2: "Two"},
    )

    assert_that(questions).is_equal_to(("Two",))
