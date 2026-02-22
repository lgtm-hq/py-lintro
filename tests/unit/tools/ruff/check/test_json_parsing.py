"""Tests for JSON output parsing in execute_ruff_check."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.tools.implementations.ruff.check import execute_ruff_check


def test_execute_ruff_check_parses_json_output_correctly(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_output: str,
) -> None:
    """Parse JSON output and create correct issue objects.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_output: Sample JSON output from ruff.
    """
    from lintro.parsers.ruff.ruff_parser import parse_ruff_output

    with (
        patch(
            "lintro.tools.implementations.ruff.check.walk_files_with_excludes",
            return_value=["test.py"],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(False, sample_ruff_json_output),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            side_effect=parse_ruff_output,
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        # Verify overall result reflects subprocess failure and issues found
        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(2)
        assert_that(result.issues).is_not_none()
        assert_that(result.issues).is_length(2)

        # Verify first issue
        first_issue = cast(RuffIssue, result.issues[0])  # type: ignore[index]  # validated via is_not_none
        assert_that(first_issue.code).is_equal_to("F401")
        assert_that(first_issue.file).is_equal_to("test.py")
        assert_that(first_issue.line).is_equal_to(1)
        assert_that(first_issue.column).is_equal_to(1)

        # Verify second issue
        second_issue = cast(RuffIssue, result.issues[1])  # type: ignore[index]  # validated via is_not_none
        assert_that(second_issue.code).is_equal_to("E501")


def test_execute_ruff_check_empty_json_output(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """Handle empty JSON output correctly.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    with (
        patch(
            "lintro.tools.implementations.ruff.check.walk_files_with_excludes",
            return_value=["test.py"],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, sample_ruff_json_empty_output),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        assert_that(result.success).is_true()
        assert_that(result.issues_count).is_equal_to(0)


def test_execute_ruff_check_parses_format_check_output(
    mock_ruff_tool: MagicMock,
    sample_ruff_format_check_output: str,
) -> None:
    """Parse format check output and create correct format issues.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_format_check_output: Sample format check output from ruff.
    """
    mock_ruff_tool.options["format_check"] = True

    from lintro.parsers.ruff.ruff_parser import parse_ruff_format_check_output

    with (
        patch(
            "lintro.tools.implementations.ruff.check.walk_files_with_excludes",
            return_value=["test.py", "src/module.py"],
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
            side_effect=parse_ruff_format_check_output,
        ),
    ):
        # First call: lint check, second call: format check.
        mock_subprocess.side_effect = [
            (True, "[]"),
            (False, sample_ruff_format_check_output),
        ]

        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        # Format check subprocess failed, so overall result should be failure.
        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(2)
