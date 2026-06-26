"""Tests for checklist display helpers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.checklist_display import (
    build_prompt_question_map,
    cleared_answers,
    enrich_review_result,
    orphan_concerns,
    questions_for_finding,
    resolve_checklist_display,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult


def test_build_prompt_question_map_uses_prompt_order() -> None:
    """Prompt ids follow enumerate order starting at 1."""
    items = [
        ChecklistItem(
            id=100,
            question="First question?",
            triggers=[],
            category=ReviewCategory.SECURITY,
            tier=1,
        ),
        ChecklistItem(
            id=200,
            question="Second question?",
            triggers=[],
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


def test_enrich_review_result_attaches_questions() -> None:
    """Checklist answers receive question text from the prompt map."""
    result = ReviewResult(
        metadata=ReviewMetadata(
            model="gpt-4o",
            provider="openai",
            context_window=128_000,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=1,
        ),
        summary="ok",
        checklist=(ChecklistAnswer(id=1, answer="yes", evidence="a.py:1"),),
        findings=(),
    )

    enriched = enrich_review_result(
        result=result,
        question_map={1: "Is auth enforced?"},
    )

    assert_that(enriched.checklist[0].question).is_equal_to("Is auth enforced?")


def test_questions_for_finding_returns_linked_prompt_questions() -> None:
    """Finding checklist_ids resolve to question strings in order."""
    finding = ReviewFinding(
        severity="P1",
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


def test_cleared_answers_returns_no_rows() -> None:
    """Cleared answers are checklist rows answered no."""
    answers = (
        ChecklistAnswer(id=1, answer="yes", evidence="a"),
        ChecklistAnswer(id=2, answer="no", evidence="b", question="Cleared?"),
    )

    cleared = cleared_answers(answers=answers)

    assert_that(cleared).is_length(1)
    assert_that(cleared[0].question).is_equal_to("Cleared?")


def test_orphan_concerns_returns_unlinked_yes_rows() -> None:
    """Orphan concerns are yes answers not referenced by findings."""
    answers = (
        ChecklistAnswer(id=1, answer="yes", evidence="linked"),
        ChecklistAnswer(id=2, answer="yes", evidence="orphan"),
        ChecklistAnswer(id=3, answer="no", evidence="cleared"),
    )
    findings = (
        ReviewFinding(
            severity="P1",
            category="security",
            file="a.py",
            line=1,
            title="Issue",
            description="desc",
            cause="cause",
            fix="fix",
            confidence="high",
            checklist_ids=(1,),
        ),
    )

    orphans = orphan_concerns(answers=answers, findings=findings)

    assert_that([answer.id for answer in orphans]).is_equal_to([2])
