"""Tests for execute_ruff_check with issues found."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.parsers.ruff.ruff_format_issue import RuffFormatIssue
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.tools.implementations.ruff.check import execute_ruff_check


def test_execute_ruff_check_with_lint_issues_returns_failure(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return failure when lint issues are found.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    lint_issues = [
        RuffIssue(
            file="test.py",
            line=1,
            column=1,
            code="F401",
            message="os imported but unused",
            fixable=True,
            fix_applicability="safe",
        ),
    ]

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(False, '[{"code": "F401"}]'),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=lint_issues,
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(1)
        assert_that(result.issues).is_not_none()
        assert_that(result.issues).is_length(1)
        first_issue = cast(RuffIssue, result.issues[0])  # type: ignore[index]  # validated via is_not_none
        assert_that(first_issue.code).is_equal_to("F401")


def test_execute_ruff_check_with_multiple_lint_issues(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return correct count when multiple lint issues are found.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    lint_issues = [
        RuffIssue(file="test.py", line=1, column=1, code="F401", message="unused"),
        RuffIssue(file="test.py", line=5, column=89, code="E501", message="too long"),
        RuffIssue(file="test.py", line=10, column=1, code="W291", message="whitespace"),
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

        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(3)
        assert_that(result.issues).is_length(3)


def test_execute_ruff_check_with_format_issues_returns_failure(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return failure when format issues are found.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool.options["format_check"] = True

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, ""),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_format_check_output",
            return_value=["test.py"],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(1)
        # Format issues should be RuffFormatIssue instances
        assert_that(result.issues).is_not_none()
        assert_that(isinstance(result.issues[0], RuffFormatIssue)).is_true()  # type: ignore[index]  # validated via is_not_none


def test_execute_ruff_check_combines_lint_and_format_issues(
    mock_ruff_tool: MagicMock,
) -> None:
    """Combine lint and format issues in result.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool.options["format_check"] = True
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
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_format_check_output",
            return_value=["other.py"],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(2)
        assert_that(result.issues).is_length(2)
