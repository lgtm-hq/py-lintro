"""Tests for output logging in execute_ruff_check."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.implementations.ruff.check import execute_ruff_check


def test_check_failure_logs_output_to_debug_only(
    mock_ruff_tool: MagicMock,
) -> None:
    """Verify check output is logged to debug only, not warning.

    When ruff check fails (exit code non-zero due to issues found),
    the raw JSON output should only be logged at debug level since
    it is already parsed and displayed as a formatted table.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    # Create output longer than 2000 chars
    long_output = "x" * 3000

    with (
        patch(
            "lintro.tools.implementations.ruff.check.walk_files_with_excludes",
            return_value=["test.py"],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(False, long_output),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
        patch("lintro.tools.implementations.ruff.check.logger") as mock_logger,
    ):
        execute_ruff_check(mock_ruff_tool, ["/test/project"])

        # Verify full output was logged to debug
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        full_output_logged = any("check full output" in call for call in debug_calls)
        assert_that(full_output_logged).described_as(
            f"Expected full output in debug calls: {debug_calls}",
        ).is_true()

        # Verify no truncation warning was logged (raw JSON should not appear)
        warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
        truncation_warning = any(
            "check failed with output" in call for call in warning_calls
        )
        assert_that(truncation_warning).described_as(
            f"Did not expect 'check failed with output' warning: {warning_calls}",
        ).is_false()


def test_format_check_failure_logs_output_to_debug_only(
    mock_ruff_tool: MagicMock,
) -> None:
    """Verify format check output is logged to debug only, not warning.

    When ruff format --check fails (exit code non-zero due to formatting issues),
    the output should only be logged at debug level since it is already
    parsed and displayed as a formatted table.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool.options["format_check"] = True

    # Create output longer than 2000 chars
    long_format_output = "Would reformat: " + "x" * 3000

    with (
        patch(
            "lintro.tools.implementations.ruff.check.walk_files_with_excludes",
            return_value=["test.py"],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
        ) as mock_subprocess,
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_format_check_output",
            return_value=[],
        ),
        patch("lintro.tools.implementations.ruff.check.logger") as mock_logger,
    ):
        # First call succeeds (lint), second call fails (format)
        mock_subprocess.side_effect = [
            (True, "[]"),
            (False, long_format_output),
        ]

        execute_ruff_check(mock_ruff_tool, ["/test/project"])

        # Verify full output was logged to debug
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        full_output_logged = any(
            "format check full output" in call for call in debug_calls
        )
        assert_that(full_output_logged).described_as(
            f"Expected full output in debug calls: {debug_calls}",
        ).is_true()

        # Verify no truncation warning was logged
        warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
        truncation_warning = any(
            "format check failed with output" in call for call in warning_calls
        )
        assert_that(truncation_warning).described_as(
            "Did not expect 'format check failed with output' warning:"
            f" {warning_calls}",
        ).is_false()


def test_check_success_does_not_log_output(
    mock_ruff_tool: MagicMock,
) -> None:
    """Verify successful check does not log output unnecessarily.

    When ruff check succeeds (no issues), there should be no output
    logged to debug since there's nothing to report.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    with (
        patch(
            "lintro.tools.implementations.ruff.check.walk_files_with_excludes",
            return_value=["test.py"],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
        patch("lintro.tools.implementations.ruff.check.logger") as mock_logger,
    ):
        execute_ruff_check(mock_ruff_tool, ["/test/project"])

        # Verify no "check full output" logged when success
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        full_output_logged = any("check full output" in call for call in debug_calls)
        assert_that(full_output_logged).described_as(
            f"Did not expect 'check full output' on success: {debug_calls}",
        ).is_false()
