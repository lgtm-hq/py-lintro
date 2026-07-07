"""Tests for result aggregation in ``lintro.utils.execution.exit_codes``."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.utils.execution.exit_codes import aggregate_tool_results


def test_aggregate_check_mode_remaining_mirrors_total_issues() -> None:
    """In check mode ``total_remaining`` mirrors ``total_issues``.

    Regression test for #1045: nothing is fixed in check mode, so reporting
    ``total_remaining`` as a constant 0 alongside a nonzero ``total_issues``
    is misleading.
    """
    results = [
        ToolResult(name="ruff", success=False, issues_count=3),
        ToolResult(name="black", success=False, issues_count=2),
    ]

    total_issues, total_fixed, total_remaining = aggregate_tool_results(
        results,
        Action.CHECK,
    )

    assert_that(total_issues).is_equal_to(5)
    assert_that(total_fixed).is_equal_to(0)
    assert_that(total_remaining).is_equal_to(5)


def test_aggregate_fix_mode_remaining_uses_remaining_counts() -> None:
    """In fix mode ``total_remaining`` sums per-tool remaining counts."""
    results = [
        ToolResult(
            name="ruff",
            success=True,
            issues_count=1,
            initial_issues_count=3,
            fixed_issues_count=2,
            remaining_issues_count=1,
        ),
    ]

    total_issues, total_fixed, total_remaining = aggregate_tool_results(
        results,
        Action.FIX,
    )

    assert_that(total_issues).is_equal_to(1)
    assert_that(total_fixed).is_equal_to(2)
    assert_that(total_remaining).is_equal_to(1)


def test_aggregate_excludes_skipped_tools() -> None:
    """Skipped tools contribute nothing to any total."""
    results = [
        ToolResult(name="ruff", success=False, issues_count=4),
        ToolResult(name="black", skipped=True, skip_reason="missing", issues_count=0),
    ]

    total_issues, _total_fixed, total_remaining = aggregate_tool_results(
        results,
        Action.CHECK,
    )

    assert_that(total_issues).is_equal_to(4)
    assert_that(total_remaining).is_equal_to(4)
