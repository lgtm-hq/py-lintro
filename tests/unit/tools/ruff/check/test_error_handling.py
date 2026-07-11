"""Tests for error handling in execute_ruff_check."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.tools.implementations.ruff.check import execute_ruff_check


def test_execute_ruff_check_handles_timeout(
    mock_ruff_tool: MagicMock,
) -> None:
    """Handle subprocess timeout gracefully.

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
            side_effect=subprocess.TimeoutExpired(cmd=["ruff"], timeout=30),
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")
        assert_that(result.issues_count).is_equal_to(1)


def test_execute_ruff_check_handles_format_timeout(
    mock_ruff_tool: MagicMock,
) -> None:
    """Handle format check timeout gracefully.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool.options["format_check"] = True
    lint_issues = [
        RuffIssue(file="test.py", line=1, column=1, code="F401", message="unused"),
    ]

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
            return_value=lint_issues,
        ),
    ):
        # First call succeeds (lint), second call times out (format)
        mock_subprocess.side_effect = [
            (False, "[]"),
            subprocess.TimeoutExpired(cmd=["ruff"], timeout=30),
        ]

        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")
        # Should include lint issues count plus timeout issue
        assert_that(result.issues_count).is_greater_than_or_equal_to(1)


def test_execute_ruff_check_subprocess_failure_respected(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return failure when subprocess fails even if no issues are parsed.

    This is a regression test for a bug where success was determined only by
    issue count, ignoring the subprocess exit code. If ruff returns non-zero
    but produces no parseable output (e.g., internal error), the result should
    still be failure.

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
            # Subprocess fails (exit code != 0) but produces empty/no output
            return_value=(False, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            # No issues parsed from output
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        # Even though no issues were parsed, subprocess failure means overall failure
        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(0)


def test_execute_ruff_check_version_check_failure(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return early when version check fails.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    version_error_result = ToolResult(
        name="ruff",
        success=True,
        output="Skipping ruff: version too old",
        issues_count=0,
    )
    mock_ruff_tool._verify_tool_version.return_value = version_error_result

    result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    assert_that(result.output).is_equal_to("Skipping ruff: version too old")
    assert_that(result.issues_count).is_equal_to(0)
