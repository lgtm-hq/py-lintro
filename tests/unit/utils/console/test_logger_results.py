"""Unit tests for ThreadSafeConsoleLogger tool result output methods.

Tests cover the print_tool_result method and its handling of various
actions and output content.
"""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.console.logger import ThreadSafeConsoleLogger


def test_print_tool_result_outputs_content(logger: ThreadSafeConsoleLogger) -> None:
    """Verify print_tool_result displays tool output when provided.

    Non-empty output should be displayed to the console, followed by
    a blank line for visual separation.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with patch.object(logger, "console_output") as mock_output:
        logger.print_tool_result("ruff", "Some output", 5)
        assert_that(mock_output.call_count).is_greater_than(0)


def test_print_tool_result_skips_empty_output(logger: ThreadSafeConsoleLogger) -> None:
    """Verify print_tool_result does nothing when output is empty.

    Empty output indicates no issues or nothing to display, so the
    method should produce no console output.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with patch.object(logger, "console_output") as mock_output:
        logger.print_tool_result("ruff", "", 0)
        mock_output.assert_not_called()


def test_print_tool_result_includes_metadata_for_check_action(
    logger: ThreadSafeConsoleLogger,
) -> None:
    """Verify print_tool_result parses metadata for CHECK action.

    When action is CHECK, raw output should be parsed for additional
    metadata messages like fixable issue counts.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_metadata_messages")
        as mock_meta,
    ):
        logger.print_tool_result(
            "ruff",
            "output",
            5,
            raw_output_for_meta="3 fixable issues",
            action=Action.CHECK,
        )
        mock_meta.assert_called_once_with("3 fixable issues")


def test_print_tool_result_skips_metadata_for_fix_action(
    logger: ThreadSafeConsoleLogger,
) -> None:
    """Verify print_tool_result skips metadata parsing for FIX action.

    FIX action already resolves issues, so metadata about fixable issues
    is not relevant and should not be displayed.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_metadata_messages")
        as mock_meta,
    ):
        logger.print_tool_result(
            "ruff",
            "output",
            5,
            raw_output_for_meta="3 fixable issues",
            action=Action.FIX,
        )
        mock_meta.assert_not_called()


def test_print_tool_result_handles_pytest_for_test_action(
    logger: ThreadSafeConsoleLogger,
) -> None:
    """Verify print_tool_result displays pytest results for TEST action.

    When the tool is pytest and action is TEST, special pytest result
    formatting should be applied to show pass/fail status clearly.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_pytest_results")
        as mock_pytest,
    ):
        logger.print_tool_result(
            "pytest",
            "test output",
            0,
            action=Action.TEST,
            success=True,
        )
        mock_pytest.assert_called_once_with("test output", True)
