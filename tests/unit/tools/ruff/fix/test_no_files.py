"""Tests for execute_ruff_fix - No files scenarios."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_no_paths(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Return success with no files message when paths list is empty.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool._prepare_execution.return_value = ruff_execution_context(
        early_result=ToolResult(
            name="ruff",
            success=True,
            output="No files to fix.",
            issues_count=0,
        ),
    )

    result = execute_ruff_fix(mock_ruff_tool, [])

    assert_that(result.success).is_true()
    assert_that(result.output).is_equal_to("No files to fix.")
    assert_that(result.issues_count).is_equal_to(0)


def test_execute_ruff_fix_no_python_files_found(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
    tmp_path: Any,
) -> None:
    """Return success with no files message when no matching files exist.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
        tmp_path: Temporary directory path for testing.
    """
    mock_ruff_tool._prepare_execution.return_value = ruff_execution_context(
        early_result=ToolResult(
            name="ruff",
            success=True,
            output="No py/pyi files found to check.",
            issues_count=0,
        ),
    )

    result = execute_ruff_fix(mock_ruff_tool, [str(tmp_path)])

    assert_that(result.success).is_true()
    assert_that(result.output).is_equal_to("No py/pyi files found to check.")
    assert_that(result.issues_count).is_equal_to(0)
