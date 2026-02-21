"""Unit tests for ThreadSafeConsoleLogger execution summary methods.

This module tests the execution summary functionality of ThreadSafeConsoleLogger,
including tests for CHECK and FIX action handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.console.logger import ThreadSafeConsoleLogger

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.unit.utils.conftest import FakeToolResult


# =============================================================================
# Execution Summary Tests - CHECK Action
# =============================================================================


def test_execution_summary_check_no_issues(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary handles check action with no issues.

    When all tools pass with zero issues, the summary should indicate
    complete success and call ASCII art with zero total issues.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=True, issues_count=0)]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.CHECK, results)
        mock_art.assert_called_once_with(total_issues=0)


def test_execution_summary_check_with_issues(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary aggregates issue counts from multiple tools.

    When multiple tools report issues, the total should be summed and
    passed to both the totals table and ASCII art display.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(success=True, issues_count=5),
        fake_tool_result_factory(success=True, issues_count=3),
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_totals_table") as mock_totals,
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.CHECK, results)
        # Should show total of 8 issues
        mock_art.assert_called_once_with(total_issues=8)
        # Verify totals table was called with correct total
        mock_totals.assert_called_once()
        call_kwargs = mock_totals.call_args.kwargs
        assert_that(call_kwargs["total_issues"]).is_equal_to(8)


def test_execution_summary_check_failed_tool_shows_minimum_issues(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary shows at least 1 issue when a tool fails.

    Failed tools should be treated as having issues even if issues_count is 0,
    ensuring the summary reflects the failure state.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=False, issues_count=0)]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.CHECK, results)
        # Should show at least 1 for art when tool failed
        mock_art.assert_called_once_with(  # noqa: E501
            total_issues=1,
        )


@pytest.mark.parametrize(
    ("issue_counts", "expected_total"),
    [
        ([0], 0),
        ([5], 5),
        ([5, 3], 8),
        ([1, 2, 3, 4], 10),
        ([0, 0, 0], 0),
    ],
)
def test_execution_summary_check_issue_aggregation(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    issue_counts: list[int],
    expected_total: int,
) -> None:
    """Verify print_execution_summary correctly sums issues from all tools.

    Different combinations of issue counts should be properly aggregated
    into the correct total.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        issue_counts: List of issue counts for each tool.
        expected_total: Expected total issues after aggregation.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(success=True, issues_count=count)
        for count in issue_counts
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.CHECK, results)
        mock_art.assert_called_once_with(
            total_issues=expected_total,
        )


# =============================================================================
# Execution Summary Tests - FIX Action
# =============================================================================


def test_execution_summary_fix_with_standardized_counts(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary uses standardized counts for fix action.

    When fixed_issues_count and remaining_issues_count are provided,
    they should be used instead of parsing from output.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=True,
            fixed_issues_count=10,
            remaining_issues_count=2,
        ),
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.FIX, results)
        mock_art.assert_called_once_with(total_issues=2)


def test_execution_summary_fix_fallback_to_issues_count(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary falls back when fixed_issues_count not provided.

    Legacy tools that don't provide fixed_issues_count should still have
    their issues_count used for the summary calculation.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=True,
            issues_count=5,
            fixed_issues_count=None,
        ),
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art"),
    ):
        # Should not raise any exception
        logger.print_execution_summary(Action.FIX, results)


def test_execution_summary_fix_failed_tool_handled(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary handles failed tools in fix action gracefully.

    Failed tools should not contribute to numeric totals to avoid misleading
    success metrics.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=False,
            issues_count=0,
            remaining_issues_count=None,
        ),
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art"),
    ):
        # Should not raise and should handle sentinel values
        logger.print_execution_summary(Action.FIX, results)


def test_execution_summary_fix_parses_remaining_from_output(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary parses remaining issues from output.

    When remaining_issues_count is not set, the method should parse
    the output string to extract remaining issue counts.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=True,
            output="5 remaining issues that cannot be auto-fixed",
            remaining_issues_count=None,
        ),
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.FIX, results)
        mock_art.assert_called_once_with(total_issues=5)


def test_execution_summary_fix_parses_cannot_autofix_from_output(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary parses 'cannot autofix' count from output.

    The 'cannot be auto-fixed' pattern should be recognized and the count
    extracted for remaining issues calculation.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    # Use the exact format the regex expects
    results = [
        fake_tool_result_factory(
            success=True,
            output="Found 3 issues that cannot be auto-fixed",
            remaining_issues_count=None,
        ),
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.FIX, results)
        mock_art.assert_called_once_with(
            total_issues=3,
        )


def test_execution_summary_fix_handles_string_sentinel_remaining(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary handles string sentinel for remaining_issues_count.

    String sentinel values (like 'N/A') should not be added to numeric
    totals to prevent type errors in calculations.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    result = fake_tool_result_factory(success=True)
    # Set a string sentinel using object attribute
    result.remaining_issues_count = "N/A"  # type: ignore[assignment]
    results = [result]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art"),
    ):
        # Should not raise or add string sentinel to numeric total
        logger.print_execution_summary(Action.FIX, results)


@pytest.mark.parametrize(
    ("fixed", "remaining", "expected_remaining"),
    [
        (10, 0, 0),
        (5, 3, 3),
        (0, 0, 0),
        (100, 10, 10),
    ],
)
def test_execution_summary_fix_various_counts(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    fixed: int,
    remaining: int,
    expected_remaining: int,
) -> None:
    """Verify print_execution_summary handles various fixed/remaining combinations.

    Different scenarios of fixed and remaining issues should be handled
    correctly with proper totals passed to ASCII art.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        fixed: Number of fixed issues.
        remaining: Number of remaining issues.
        expected_remaining: Expected remaining issues total.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=True,
            fixed_issues_count=fixed,
            remaining_issues_count=remaining,
        ),
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.FIX, results)
        mock_art.assert_called_once_with(total_issues=expected_remaining)
