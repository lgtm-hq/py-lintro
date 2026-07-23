"""Tests for loop-aware execution in the parallel executor.

Verify that ``run_tools_parallel`` works both from a normal synchronous
context (CLI path) and from within an already-running event loop (embedded /
library usage), never raising the nested-loop ``RuntimeError``.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from assertpy import assert_that

import lintro.utils.async_tool_executor as async_module
import lintro.utils.execution.parallel_executor as parallel_module
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.execution.parallel_executor import (
    _run_coroutine_blocking,
    run_tools_parallel,
)
from lintro.utils.unified_config import UnifiedConfigManager


class _FakeExecutor:
    """Minimal stand-in for ``AsyncToolExecutor`` avoiding real tool runs."""

    def __init__(self, max_workers: int) -> None:
        """Store the requested worker count.

        Args:
            max_workers: Maximum parallel workers (unused by the fake).
        """
        self.max_workers = max_workers
        self.shutdown_called = False

    async def run_tools_parallel(
        self,
        tools: list[tuple[str, Any]],
        paths: list[str],
        action: Action,
        on_result: Any = None,
        max_fix_retries: int = 3,
    ) -> list[tuple[str, ToolResult]]:
        """Return a successful result for each tool without subprocess work.

        Args:
            tools: List of ``(tool_name, tool)`` tuples.
            paths: Paths to process (unused).
            action: Action to perform (unused).
            on_result: Optional per-tool completion callback.
            max_fix_retries: Fix retry budget (unused).

        Returns:
            A ``(tool_name, ToolResult)`` tuple for each input tool.
        """
        out: list[tuple[str, ToolResult]] = []
        for name, _tool in tools:
            result = ToolResult(
                name=name,
                success=True,
                output="ok",
                issues_count=0,
            )
            if on_result is not None:
                on_result(name, result)
            out.append((name, result))
        return out

    def shutdown(self) -> None:
        """Record that shutdown was requested."""
        self.shutdown_called = True


@pytest.fixture
def patched_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch heavy collaborators so ``run_tools_parallel`` stays deterministic.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(async_module, "AsyncToolExecutor", _FakeExecutor)
    monkeypatch.setattr(
        async_module,
        "get_parallel_batches",
        lambda tools, tool_manager: [list(tools)],
    )
    monkeypatch.setattr(
        tool_manager,
        "get_tool",
        lambda name: object(),
    )
    monkeypatch.setattr(
        parallel_module,
        "configure_tool_for_execution",
        lambda **kwargs: kwargs["tool"],
    )


def _invoke() -> list[ToolResult]:
    """Call ``run_tools_parallel`` with a single mock tool.

    Returns:
        The list of ``ToolResult`` objects produced by the executor.
    """
    return run_tools_parallel(
        tools_to_run=["mock_tool"],
        paths=["."],
        action=Action.CHECK,
        config_manager=cast(UnifiedConfigManager, object()),
        tool_option_dict={},
        exclude=None,
        include_venv=False,
        post_tools=set(),
        max_workers=2,
    )


def test_parallel_executor_from_sync_context(patched_parallel: None) -> None:
    """The executor runs normally from a synchronous context.

    Args:
        patched_parallel: Fixture patching heavy collaborators.
    """
    results = _invoke()

    assert_that(results).is_length(1)
    assert_that(results[0].name).is_equal_to("mock_tool")
    assert_that(results[0].success).is_true()


def test_parallel_executor_inside_running_loop(patched_parallel: None) -> None:
    """The executor returns results when a loop is already running.

    Args:
        patched_parallel: Fixture patching heavy collaborators.
    """

    async def caller() -> list[ToolResult]:
        # A loop is running in this thread; the executor must not crash.
        return _invoke()

    results = asyncio.run(caller())

    assert_that(results).is_length(1)
    assert_that(results[0].name).is_equal_to("mock_tool")
    assert_that(results[0].success).is_true()


def test_run_coroutine_blocking_from_sync_context() -> None:
    """The helper runs a coroutine directly when no loop is running."""

    async def coro() -> int:
        return 21 * 2

    assert_that(_run_coroutine_blocking(coro())).is_equal_to(42)


def test_run_coroutine_blocking_inside_running_loop() -> None:
    """The helper offloads to a worker thread when a loop is running."""

    async def coro() -> str:
        return "done"

    async def caller() -> str:
        return _run_coroutine_blocking(coro())

    assert_that(asyncio.run(caller())).is_equal_to("done")
