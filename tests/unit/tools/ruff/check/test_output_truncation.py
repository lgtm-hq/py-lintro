"""Tests for output logging in execute_ruff_check."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.implementations.ruff.check import execute_ruff_check


def _messages_at(records: list[tuple[str, str]], level: str) -> list[str]:
    """Select the captured log messages emitted at one level.

    Args:
        records: Captured ``(level, message)`` pairs.
        level: Level name to select.

    Returns:
        The messages logged at that level, in order.
    """
    return [message for record_level, message in records if record_level == level]


def test_check_failure_logs_output_to_debug_only(
    mock_ruff_tool: MagicMock,
    loguru_records: list[tuple[str, str]],
) -> None:
    """Log the raw ruff output at DEBUG and never at WARNING.

    The raw JSON is parsed into a formatted table for the console, so a failed
    check must not push it through a user-facing warning.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        loguru_records: Captured loguru records for the test.
    """
    long_output = "x" * 3000

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(False, long_output),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        execute_ruff_check(mock_ruff_tool, ["/test/project"])

    debug_messages = _messages_at(loguru_records, "DEBUG")
    assert_that(
        [message for message in debug_messages if "check full output" in message],
    ).is_not_empty()
    assert_that("\n".join(debug_messages)).contains(long_output)
    # Assert on the payload itself: no phrase like "check failed with output"
    # is logged anywhere in lintro, so forbidding that string proved nothing.
    # A real diagnostic WARNING does fire here (parse_ruff_output returned no
    # issues), so "zero warnings" would be the wrong assertion (#2315).
    assert_that("\n".join(_messages_at(loguru_records, "WARNING"))).does_not_contain(
        long_output,
    )


def test_format_check_failure_logs_output_to_debug_only(
    mock_ruff_tool: MagicMock,
    loguru_records: list[tuple[str, str]],
) -> None:
    """Log the raw ruff format output at DEBUG and never at WARNING.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        loguru_records: Captured loguru records for the test.
    """
    mock_ruff_tool.options["format_check"] = True
    long_format_output = "Would reformat: " + "x" * 3000

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            side_effect=[(True, "[]"), (False, long_format_output)],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_format_check_output",
            return_value=[],
        ),
    ):
        execute_ruff_check(mock_ruff_tool, ["/test/project"])

    debug_messages = _messages_at(loguru_records, "DEBUG")
    assert_that(
        [
            message
            for message in debug_messages
            if "format check full output" in message
        ],
    ).is_not_empty()
    assert_that("\n".join(debug_messages)).contains(long_format_output)
    assert_that("\n".join(_messages_at(loguru_records, "WARNING"))).does_not_contain(
        long_format_output,
    )


def test_check_success_does_not_log_output(
    mock_ruff_tool: MagicMock,
    loguru_records: list[tuple[str, str]],
) -> None:
    """Log no ruff output when the check reports no issues.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        loguru_records: Captured loguru records for the test.
    """
    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    assert_that("\n".join(_messages_at(loguru_records, "DEBUG"))).does_not_contain(
        "check full output",
    )
    assert_that(result.success).is_true()
