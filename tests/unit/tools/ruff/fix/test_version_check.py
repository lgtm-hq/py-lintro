"""Tests for execute_ruff_fix - Version check scenarios."""

from __future__ import annotations

from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_version_check_fails(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return version error result when the prepared context reports a failure.

    Version checking now happens inside the shared ``prepare``
    pipeline, which surfaces the failure via ``early_result``.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    version_error = ToolResult(
        name="ruff",
        success=True,
        output="Ruff version too old",
        issues_count=0,
    )
    mock_ruff_tool.prepare.return_value = version_error

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result).is_equal_to(version_error)
