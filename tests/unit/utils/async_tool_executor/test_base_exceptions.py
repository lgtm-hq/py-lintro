"""Tests for BaseException handling in ``run_tools_parallel``.

These cover the fatal control-flow exceptions that ``asyncio.gather`` returns
as values under ``return_exceptions=True`` (``CancelledError``,
``KeyboardInterrupt``, ``SystemExit``), plus malformed results that must never
enter the success branch.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.utils.async_tool_executor import AsyncToolExecutor

from .conftest import MockToolDefinition, MockToolPlugin


def _tool_raising(
    name: str,
    exc: BaseException,
) -> tuple[str, BaseToolPlugin]:
    """Build a mock tool whose ``check`` raises the given exception.

    Args:
        name: Name to assign to the mock tool.
        exc: Exception instance to raise from ``check``.

    Returns:
        A ``(tool_name, tool)`` tuple ready for ``run_tools_parallel``.
    """
    tool = MockToolPlugin(definition=MockToolDefinition(name=name))

    def raising_check(
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        raise exc

    object.__setattr__(tool, "check", raising_check)
    return (name, cast(BaseToolPlugin, tool))


def test_cancelled_error_reraised(executor: AsyncToolExecutor) -> None:
    """A task raising ``CancelledError`` propagates cancellation.

    Args:
        executor: AsyncToolExecutor fixture.
    """
    tools = [_tool_raising(name="cancel_tool", exc=asyncio.CancelledError())]

    async def run_test() -> Any:
        return await executor.run_tools_parallel(
            tools=tools,
            paths=["."],
            action=Action.CHECK,
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_test())


def test_keyboard_interrupt_propagates(executor: AsyncToolExecutor) -> None:
    """A task raising ``KeyboardInterrupt`` propagates to the caller.

    Args:
        executor: AsyncToolExecutor fixture.
    """
    tools = [_tool_raising(name="kbi_tool", exc=KeyboardInterrupt())]

    async def run_test() -> Any:
        return await executor.run_tools_parallel(
            tools=tools,
            paths=["."],
            action=Action.CHECK,
        )

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(run_test())


def test_system_exit_propagates(executor: AsyncToolExecutor) -> None:
    """A task raising ``SystemExit`` propagates to the caller.

    Args:
        executor: AsyncToolExecutor fixture.
    """
    tools = [_tool_raising(name="exit_tool", exc=SystemExit(2))]

    async def run_test() -> Any:
        return await executor.run_tools_parallel(
            tools=tools,
            paths=["."],
            action=Action.CHECK,
        )

    with pytest.raises(SystemExit):
        asyncio.run(run_test())


def test_tool_exception_maps_to_failed_result(
    executor: AsyncToolExecutor,
) -> None:
    """A per-tool ``ValueError`` becomes a failed ``ToolResult``.

    Args:
        executor: AsyncToolExecutor fixture.
    """
    tools = [_tool_raising(name="value_tool", exc=ValueError("boom"))]

    async def run_test() -> Any:
        return await executor.run_tools_parallel(
            tools=tools,
            paths=["."],
            action=Action.CHECK,
        )

    results = asyncio.run(run_test())

    assert_that(results).is_length(1)
    name, result = results[0]
    assert_that(name).is_equal_to("value_tool")
    assert_that(result.success).is_false()
    assert_that(result.output).contains("Parallel execution failed", "boom")


def test_malformed_result_does_not_corrupt_results(
    executor: AsyncToolExecutor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-tuple gather result never lands in the success branch.

    Args:
        executor: AsyncToolExecutor fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    sentinel = object()

    async def fake_gather(*args: Any, **kwargs: Any) -> list[Any]:
        return [sentinel]

    monkeypatch.setattr(asyncio, "gather", fake_gather)

    tools = cast(
        list[tuple[str, BaseToolPlugin]],
        [("mock_tool", MockToolPlugin(definition=MockToolDefinition()))],
    )

    async def run_test() -> Any:
        return await executor.run_tools_parallel(
            tools=tools,
            paths=["."],
            action=Action.CHECK,
        )

    results = asyncio.run(run_test())

    assert_that(results).is_length(1)
    name, result = results[0]
    assert_that(name).is_equal_to("mock_tool")
    assert_that(result.success).is_false()
    assert_that(result.output).contains("malformed result")
