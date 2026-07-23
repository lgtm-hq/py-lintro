"""Tests for execute_ruff_fix - Version check scenarios."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_version_check_fails(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Return version error result when the prepared context reports a failure.

    Version checking now happens inside the shared ``_prepare_execution``
    pipeline, which surfaces the failure via ``early_result``.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    version_error = ToolResult(
        name="ruff",
        success=True,
        output="Ruff version too old",
        issues_count=0,
    )
    mock_ruff_tool._prepare_execution.return_value = ruff_execution_context(
        early_result=version_error,
    )

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result).is_equal_to(version_error)
