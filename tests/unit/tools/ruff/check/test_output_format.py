"""Tests for output format in execute_ruff_check."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.tools.implementations.ruff.check import execute_ruff_check


def test_execute_ruff_check_output_is_none_on_success(
    mock_ruff_tool: MagicMock,
) -> None:
    """Output summary should be None (suppressed) for successful run.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
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

        # Output is suppressed per the source code comment
        assert_that(result.output).is_none()


def test_execute_ruff_check_output_is_none_with_issues(
    mock_ruff_tool: MagicMock,
) -> None:
    """Output summary should be None even with issues (formatters handle display).

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    lint_issues = [
        RuffIssue(file="test.py", line=1, column=1, code="F401", message="unused"),
    ]

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(False, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=lint_issues,
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        # Verify failure when subprocess fails and issues are found
        assert_that(result.success).is_false()
        # Output is suppressed - formatters handle the display
        assert_that(result.output).is_none()
