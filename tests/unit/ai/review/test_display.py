"""Tests for review terminal display."""

from __future__ import annotations

from assertpy import assert_that
from rich.console import Console

from lintro.ai.review.display import render_review_terminal
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
