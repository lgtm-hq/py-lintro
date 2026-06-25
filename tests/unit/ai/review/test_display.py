"""Tests for review terminal display."""

from __future__ import annotations

from assertpy import assert_that
from rich.console import Console

from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult


def test_render_review_terminal_with_empty_findings() -> None:
    """Terminal rendering succeeds when no findings are present."""
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
            checklist_items=0,
        ),
        summary="Safe to merge.",
        checklist=(),
        findings=(),
    )
    console = Console(record=True)
    render_review_terminal(result=result, console=console)

    assert_that(console.export_text()).contains("Safe to merge")


def test_render_review_terminal_orders_p1_before_p2(
    sample_review_result: ReviewResult,
) -> None:
    """P1 findings appear before P2 findings in terminal output."""
    console = Console(record=True)
    render_review_terminal(result=sample_review_result, console=console)
    text = console.export_text()

    assert_that(text.index("P1")).is_less_than(text.index("P2"))


def test_render_review_terminal_default_hides_checklist_table(
    sample_review_result: ReviewResult,
) -> None:
    """Default output omits the legacy checklist table."""
    console = Console(record=True)
    render_review_terminal(result=sample_review_result, console=console)
    text = console.export_text()

    assert_that(text).contains("Structured checks: 3")
    assert_that(text).does_not_contain("Checklist")
    assert_that(text).does_not_contain("ID | Answer | Evidence")
    assert_that(text).does_not_contain("Review questions:")


def test_render_review_terminal_linked_shows_questions_under_findings(
    sample_review_result: ReviewResult,
) -> None:
    """Linked mode shows review questions under findings."""
    console = Console(record=True)
    question_map = {
        1: "Does unknown status fail closed?",
        2: "Are access paths covered by tests?",
        3: "Is migration documented?",
    }
    render_review_terminal(
        result=sample_review_result,
        console=console,
        checklist_display=ChecklistDisplay.LINKED,
        question_map=question_map,
    )
    text = console.export_text()

    assert_that(text).contains("Review questions:")
    assert_that(text).contains("Does unknown status fail closed?")
    assert_that(text).does_not_contain("Cleared checks")


def test_render_review_terminal_all_shows_appendix(
    sample_review_result: ReviewResult,
) -> None:
    """All mode includes cleared and orphan checklist appendices."""
    console = Console(record=True)
    question_map = {
        1: "Does unknown status fail closed?",
        2: "Are access paths covered by tests?",
        3: "Is migration documented?",
    }
    render_review_terminal(
        result=sample_review_result,
        console=console,
        checklist_display=ChecklistDisplay.ALL,
        question_map=question_map,
    )
    text = console.export_text()

    assert_that(text).contains("Cleared checks (1)")
    assert_that(text).contains("Are access paths covered by tests?")
    assert_that(text).contains("Checklist concerns without findings (1)")
    assert_that(text).contains("Is migration documented?")
    assert_that(text).does_not_contain("(none — good)")
