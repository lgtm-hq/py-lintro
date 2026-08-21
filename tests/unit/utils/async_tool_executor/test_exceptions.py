"""Tests for exception handling in parallel execution."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.utils.async_tool_executor import AsyncToolExecutor

from .conftest import MockToolDefinition, MockToolPlugin


def test_exception_in_tool_creates_failed_result(
    executor: AsyncToolExecutor,
) -> None:
    """Test that exceptions in tools create failed results.

    Args:
        executor: AsyncToolExecutor fixture.
    """

    def raising_check(
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        raise RuntimeError("Test exception")

    tool = MockToolPlugin(definition=MockToolDefinition(name="raise_tool"))
    # Use object.__setattr__ to bypass dataclass method assignment restriction
    object.__setattr__(tool, "check", raising_check)

    async def run_test() -> Any:
        # MockToolPlugin implements the same interface as BaseToolPlugin for testing
        return await executor.run_tools_parallel(
            tools=cast(list[tuple[str, BaseToolPlugin]], [("raise_tool", tool)]),
            paths=["."],
            action=Action.CHECK,
        )

    results = asyncio.run(run_test())

    assert_that(results).is_length(1)
    name, result = results[0]
    assert_that(name).is_equal_to("raise_tool")
    assert_that(result.success).is_false()
    assert_that(result.output).contains("Parallel execution failed")
    assert_that(result.duration_seconds).is_not_none()
    assert_that(result.duration_seconds).is_greater_than_or_equal_to(0.0)
