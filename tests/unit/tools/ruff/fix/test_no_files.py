"""Tests for execute_ruff_fix - No files scenarios."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_no_paths(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return success with no files message when paths list is empty.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    early_result = ToolResult(
        name="ruff",
        success=True,
        output="No files to fix.",
        issues_count=0,
    )
    mock_ruff_tool.prepare.return_value = early_result

    result = execute_ruff_fix(mock_ruff_tool, [])

    assert_that(result).is_same_as(early_result)


def test_execute_ruff_fix_no_python_files_found(
    mock_ruff_tool: MagicMock,
    tmp_path: Any,
) -> None:
    """Return success with no files message when no matching files exist.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        tmp_path: Temporary directory path for testing.
    """
    early_result = ToolResult(
        name="ruff",
        success=True,
        output="No py/pyi files found to check.",
        issues_count=0,
    )
    mock_ruff_tool.prepare.return_value = early_result

    result = execute_ruff_fix(mock_ruff_tool, [str(tmp_path)])

    assert_that(result).is_same_as(early_result)
