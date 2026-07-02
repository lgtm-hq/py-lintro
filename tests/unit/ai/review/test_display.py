"""Tests for review terminal display."""

from __future__ import annotations

from assertpy import assert_that
from rich.console import Console

from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.review_finding import ReviewFinding
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


def test_render_review_terminal_orders_p1_before_p2() -> None:
    """P1 findings appear before P2 findings in terminal output."""
    result = ReviewResult(
        metadata=ReviewMetadata(
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            context_window=200_000,
            depth=2,
            chunks_total=2,
            chunks_current=2,
            files_reviewed=3,
            files_total=3,
            checklist_items=2,
            token_usage={"prompt": 1000, "completion": 200, "total": 1200},
            cost_estimate_usd=0.05,
            base_ref="main",
            head_ref="feature",
            timestamp="2026-06-24T10:00:00+00:00",
        ),
        summary="Merge with fixes.",
        checklist=(
            ChecklistAnswer(id=1, answer="yes", evidence="src/main.py:10"),
            ChecklistAnswer(id=2, answer="no", evidence="none"),
        ),
        findings=(
            ReviewFinding(
                severity="P1",
                category="security",
                file="src/main.py",
                line=10,
                title="Fail-open default",
                description="Unknown status grants access",
                cause="else branch returns Active",
                fix="Default to Expired",
                confidence="high",
                checklist_ids=(1,),
            ),
            ReviewFinding(
                severity="P2",
                category="test-gap",
                file="tests/test_main.py",
                line=5,
                title="Missing access test",
                description="No test for unknown status",
                cause="Test gap",
                fix="Add unit test",
                confidence="medium",
                checklist_ids=(2,),
            ),
        ),
    )
    console = Console(record=True)
    render_review_terminal(result=result, console=console)
    text = console.export_text()

    assert_that(text.index("P1")).is_less_than(text.index("P2"))
