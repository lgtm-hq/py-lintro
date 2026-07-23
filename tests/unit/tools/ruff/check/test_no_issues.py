"""Tests for execute_ruff_check with no issues found."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.implementations.ruff.check import execute_ruff_check


def test_execute_ruff_check_no_issues_returns_success(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return success when no lint issues are found.

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

        assert_that(result.success).is_true()
        assert_that(result.issues_count).is_equal_to(0)
        assert_that(result.name).is_equal_to("ruff")


def test_execute_ruff_check_no_issues_with_format_check(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return success when no lint or format issues are found.

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
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        assert_that(result.success).is_true()
        assert_that(result.issues_count).is_equal_to(0)


def test_execute_ruff_check_empty_paths_returns_no_files_message(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Return no files message when the prepared context short-circuits.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool._prepare_execution.return_value = ruff_execution_context(
        early_result=ToolResult(
            name="ruff",
            success=True,
            output="No files to check.",
            issues_count=0,
        ),
    )

    result = execute_ruff_check(mock_ruff_tool, [])

    assert_that(result.success).is_true()
    assert_that(result.output).is_equal_to("No files to check.")
    assert_that(result.issues_count).is_equal_to(0)


def test_execute_ruff_check_no_python_files_found(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Return no files message when no matching files are discovered.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool._prepare_execution.return_value = ruff_execution_context(
        early_result=ToolResult(
            name="ruff",
            success=True,
            output="No py/pyi files found to check.",
            issues_count=0,
        ),
    )

    result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    assert_that(result.success).is_true()
    assert_that(result.output).is_equal_to("No py/pyi files found to check.")
    assert_that(result.issues_count).is_equal_to(0)
