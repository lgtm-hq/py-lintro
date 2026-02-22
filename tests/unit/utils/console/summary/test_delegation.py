"""Unit tests for ThreadSafeConsoleLogger summary delegation methods.

This module tests the delegation functionality of ThreadSafeConsoleLogger summary
methods,
including summary table, final status, and ASCII art delegation.
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
# Summary Table Delegation Tests
# =============================================================================


def test_print_summary_table_delegates_to_module_function(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify _print_summary_table delegates to print_summary_table function.

    The method should pass through all parameters to the module-level
    print_summary_table function.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory()]

    with patch("lintro.utils.summary_tables.print_summary_table") as mock_print:
        logger._print_summary_table(Action.CHECK, results)
        mock_print.assert_called_once()


def test_print_summary_table_converts_string_action(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify _print_summary_table converts string action to Action enum.

    String action values should be normalized to Action enum instances
    before being passed to the underlying function.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory()]

    with patch("lintro.utils.summary_tables.print_summary_table") as mock_print:
        logger._print_summary_table("check", results)
        mock_print.assert_called_once()
        call_kwargs = mock_print.call_args.kwargs
        assert_that(call_kwargs["action"]).is_equal_to(Action.CHECK)


@pytest.mark.parametrize(
    ("action_str", "expected_action"),
    [
        ("check", Action.CHECK),
        ("fix", Action.FIX),
        ("fmt", Action.FIX),
        ("test", Action.TEST),
    ],
)
def test_print_summary_table_action_normalization(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    action_str: str,
    expected_action: Action,
) -> None:
    """Verify _print_summary_table normalizes various action string formats.

    Different string representations of actions should all be correctly
    converted to their corresponding Action enum values.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        action_str: String representation of the action.
        expected_action: Expected Action enum value.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory()]

    with patch("lintro.utils.summary_tables.print_summary_table") as mock_print:
        logger._print_summary_table(action_str, results)
        call_kwargs = mock_print.call_args.kwargs
        assert_that(call_kwargs["action"]).is_equal_to(expected_action)


# =============================================================================
# Totals Table Delegation Tests
# =============================================================================


def test_print_totals_table_delegates_to_module_function() -> None:
    """Verify _print_totals_table delegates to print_totals_table function.

    The method should pass through all parameters to the module-level
    print_totals_table function.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.summary_tables.print_totals_table") as mock_print:
        logger._print_totals_table(
            action=Action.CHECK,
            total_issues=5,
            affected_files=2,
        )
        mock_print.assert_called_once_with(
            console_output_func=logger.console_output,
            action=Action.CHECK,
            total_issues=5,
            total_fixed=0,
            total_remaining=0,
            affected_files=2,
            severity_errors=0,
            severity_warnings=0,
            severity_info=0,
            total_ai_applied=0,
            total_ai_verified=0,
        )


def test_print_totals_table_delegates_fix_mode() -> None:
    """Verify _print_totals_table delegates FIX mode parameters correctly.

    FIX mode should pass total_fixed and total_remaining to the
    underlying function.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.summary_tables.print_totals_table") as mock_print:
        logger._print_totals_table(
            action=Action.FIX,
            total_fixed=10,
            total_remaining=3,
            affected_files=5,
        )
        mock_print.assert_called_once_with(
            console_output_func=logger.console_output,
            action=Action.FIX,
            total_issues=0,
            total_fixed=10,
            total_remaining=3,
            affected_files=5,
            severity_errors=0,
            severity_warnings=0,
            severity_info=0,
            total_ai_applied=0,
            total_ai_verified=0,
        )


@pytest.mark.parametrize(
    ("action", "kwargs"),
    [
        (Action.CHECK, {"total_issues": 0, "affected_files": 0}),
        (Action.CHECK, {"total_issues": 10, "affected_files": 5}),
        (Action.FIX, {"total_fixed": 5, "total_remaining": 2, "affected_files": 3}),
        (Action.TEST, {"total_issues": 4, "affected_files": 1}),
    ],
)
def test_print_totals_table_various_inputs(
    action: Action,
    kwargs: dict[str, int],
) -> None:
    """Verify _print_totals_table handles various action and parameter combos.

    Args:
        action: Action type to test.
        kwargs: Keyword arguments to pass to the method.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.summary_tables.print_totals_table") as mock_print:
        logger._print_totals_table(action=action, **kwargs)
        mock_print.assert_called_once()


# =============================================================================
# Final Status Delegation Tests
# =============================================================================


def test_print_final_status_delegates_to_module_function() -> None:
    """Verify _print_final_status delegates to print_final_status function.

    The method should pass the console output function, action, and
    total issues to the module-level function.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.console.logger.print_final_status") as mock_print:
        logger._print_final_status(Action.CHECK, 5)
        mock_print.assert_called_once()


def test_print_final_status_converts_string_action() -> None:
    """Verify _print_final_status accepts string action values.

    String actions should be passed through and handled by the underlying
    function's normalization logic.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.console.logger.print_final_status") as mock_print:
        logger._print_final_status("fmt", 3)
        mock_print.assert_called_once()


@pytest.mark.parametrize(
    ("action", "total_issues"),
    [
        (Action.CHECK, 0),
        (Action.CHECK, 10),
        (Action.FIX, 0),
        (Action.FIX, 5),
        ("check", 3),
        ("fmt", 7),
    ],
)
def test_print_final_status_various_inputs(
    action: Action | str,
    total_issues: int,
) -> None:
    """Verify _print_final_status handles various action and issue combinations.

    Both enum and string action values with different issue counts should
    be properly delegated.


    Args:
        action: Action type (enum or string).
        total_issues: Number of total issues.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.console.logger.print_final_status") as mock_print:
        logger._print_final_status(action, total_issues)
        mock_print.assert_called_once()


# =============================================================================
# Final Status Format Delegation Tests
# =============================================================================


def test_print_final_status_format_delegates_correctly() -> None:
    """Verify _print_final_status_format delegates with correct parameters.

    The method should pass the console output function, total fixed,
    and total remaining to the module-level function.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.console.logger.print_final_status_format") as mock_print:
        logger._print_final_status_format(10, 2)
        mock_print.assert_called_once_with(
            console_output_func=logger.console_output,
            total_fixed=10,
            total_remaining=2,
        )


@pytest.mark.parametrize(
    ("total_fixed", "total_remaining"),
    [
        (0, 0),
        (5, 0),
        (0, 3),
        (10, 5),
        (100, 50),
    ],
)
def test_print_final_status_format_various_counts(
    total_fixed: int,
    total_remaining: int,
) -> None:
    """Verify _print_final_status_format handles various count combinations.

    Different fixed and remaining combinations should be properly passed
    to the underlying function.


    Args:
        total_fixed: Number of fixed issues.
        total_remaining: Number of remaining issues.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.console.logger.print_final_status_format") as mock_print:
        logger._print_final_status_format(total_fixed, total_remaining)
        mock_print.assert_called_once_with(
            console_output_func=logger.console_output,
            total_fixed=total_fixed,
            total_remaining=total_remaining,
        )


# =============================================================================
# ASCII Art Delegation Tests
# =============================================================================


def test_print_ascii_art_delegates_correctly() -> None:
    """Verify _print_ascii_art delegates with correct parameters.

    The method should pass the console output function and issue count
    to the module-level print_ascii_art function.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.console.logger.print_ascii_art") as mock_print:
        logger._print_ascii_art(5)
        mock_print.assert_called_once_with(
            console_output_func=logger.console_output,
            issue_count=5,
        )


@pytest.mark.parametrize("issue_count", [0, 1, 5, 10, 100])
def test_print_ascii_art_various_counts(issue_count: int) -> None:
    """Verify _print_ascii_art handles various issue counts.

    Different issue counts should be properly passed to display
    either success or failure ASCII art.


    Args:
        issue_count: Number of issues to display.
    """
    logger = ThreadSafeConsoleLogger()

    with patch("lintro.utils.console.logger.print_ascii_art") as mock_print:
        logger._print_ascii_art(issue_count)
        mock_print.assert_called_once_with(
            console_output_func=logger.console_output,
            issue_count=issue_count,
        )


# =============================================================================
# Integration Tests - Full Execution Summary Flow
# =============================================================================


def test_execution_summary_outputs_header_and_border(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary outputs properly styled header.

    The execution summary should begin with a styled header including
    the section title and border for visual clarity.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=True, issues_count=0)]

    with (
        patch.object(logger, "console_output") as mock_output,
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art"),
    ):
        logger.print_execution_summary(Action.CHECK, results)
        # Should have multiple output calls including header
        assert_that(mock_output.call_count).is_greater_than(0)


def test_execution_summary_calls_all_components(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary invokes all required components.

    The method should call summary table and ASCII art display as part
    of the complete execution summary output.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=True, issues_count=3)]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table") as mock_table,
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.CHECK, results)
        mock_table.assert_called_once()
        mock_art.assert_called_once()


def test_execution_summary_empty_results_handled(
    console_capture: tuple[Callable[[str], None], list[str]],
) -> None:
    """Verify print_execution_summary handles empty results list gracefully.

    Even with no tool results, the summary should complete without errors
    and show appropriate totals (zero).


    Args:
        console_capture: Fixture for capturing console output.
    """
    logger = ThreadSafeConsoleLogger()

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art") as mock_art,
    ):
        logger.print_execution_summary(Action.CHECK, [])
        mock_art.assert_called_once_with(total_issues=0)


@pytest.mark.parametrize(
    "action",
    [Action.CHECK, Action.FIX, Action.TEST],
)
def test_execution_summary_all_action_types(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    action: Action,
) -> None:
    """Verify print_execution_summary handles all action types.

    CHECK, FIX, and TEST actions should all produce valid summary output
    without errors.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        action: Action type to test.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=True, issues_count=0)]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art"),
    ):
        # Should not raise for any action type
        logger.print_execution_summary(action, results)
