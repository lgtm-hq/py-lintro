"""Async tool execution for parallel linting.

This module provides functionality to run multiple linting tools in parallel
using asyncio and ThreadPoolExecutor for subprocess isolation.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from lintro.enums.action import Action
    from lintro.models.core.tool_result import ToolResult
    from lintro.plugins.base import BaseToolPlugin


def _get_default_max_workers() -> int:
    """Get default max workers based on CPU count.

    Returns:
        Number of CPUs available, clamped between 1 and 32.
    """
    cpu_count = os.cpu_count() or 4
    return max(1, min(cpu_count, 32))


@dataclass
class AsyncToolExecutor:
    """Execute tools in parallel using a thread pool.

    Tools are executed in a ThreadPoolExecutor to avoid blocking the event loop,
    since each tool runs as a subprocess which is inherently blocking.

    Attributes:
        max_workers: Maximum number of concurrent tool executions (default: CPU count).
    """

    max_workers: int = field(default_factory=_get_default_max_workers)
    _executor: ThreadPoolExecutor | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Initialize the thread pool executor."""
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)

    def __enter__(self) -> AsyncToolExecutor:
        """Enter context manager.

        Returns:
            AsyncToolExecutor: This executor instance.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit context manager and cleanup resources.

        Args:
            exc_type: Exception type if an exception was raised.
            exc_val: Exception instance if an exception was raised.
            exc_tb: Traceback if an exception was raised.
        """
        self.shutdown()

    async def run_tool_async(
        self,
        tool: BaseToolPlugin,
        paths: list[str],
        action: Action,
        options: dict[str, Any] | None = None,
        max_fix_retries: int = 3,
    ) -> ToolResult:
        """Run a single tool asynchronously.

        Args:
            tool: The tool plugin to execute.
            paths: List of file paths to process.
            action: The action to perform (check or fix).
            options: Additional options to pass to the tool.
            max_fix_retries: Maximum fix→verify convergence cycles.

        Returns:
            ToolResult: The result of tool execution.

        Raises:
            RuntimeError: If the executor has been shut down.
        """
        from lintro.enums.action import Action

        if self._executor is None:
            raise RuntimeError("Executor has been shut down")

        loop = asyncio.get_running_loop()
        opts = options or {}

        if action == Action.FIX:
            from lintro.utils.tool_executor import _run_fix_with_retry

            logger.debug(f"Starting async execution of {tool.definition.name}")
            result = await loop.run_in_executor(
                self._executor,
                _run_fix_with_retry,
                tool,
                paths,
                opts,
                max_fix_retries,
            )
        else:
            logger.debug(f"Starting async execution of {tool.definition.name}")
            result = await loop.run_in_executor(
                self._executor,
                tool.check,
                paths,
                opts,
            )
        logger.debug(f"Completed async execution of {tool.definition.name}")

        return result

    async def run_tools_parallel(
        self,
        tools: list[tuple[str, BaseToolPlugin]],
        paths: list[str],
        action: Action,
        options_per_tool: dict[str, dict[str, Any]] | None = None,
        on_result: Callable[[str, ToolResult], None] | None = None,
        max_fix_retries: int = 3,
    ) -> list[tuple[str, ToolResult]]:
        """Run multiple tools in parallel.

        Args:
            tools: List of (tool_name, tool_instance) tuples.
            paths: List of file paths to process.
            action: The action to perform.
            options_per_tool: Optional dict mapping tool names to their options.
            on_result: Optional callback called when each tool completes.
            max_fix_retries: Maximum fix→verify convergence cycles.

        Returns:
            List of (tool_name, ToolResult) tuples in completion order.
        """
        options = options_per_tool or {}

        async def run_with_name(
            name: str,
            tool: BaseToolPlugin,
        ) -> tuple[str, ToolResult]:
            """Run tool and return result with name.

            Args:
                name: Name of the tool.
                tool: Tool plugin instance to run.

            Returns:
                Tuple of (tool_name, ToolResult).
            """
            tool_opts = options.get(name, {})
            result = await self.run_tool_async(
                tool,
                paths,
                action,
                tool_opts,
                max_fix_retries=max_fix_retries,
            )
            if on_result:
                on_result(name, result)
            return (name, result)

        tasks = [run_with_name(name, tool) for name, tool in tools]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any exceptions that occurred
        processed_results: list[tuple[str, ToolResult]] = []
        for i, result in enumerate(results):
            tool_name = tools[i][0]
            if isinstance(result, Exception):
                logger.error(f"Tool {tool_name} failed with exception: {result}")
                # Create a failed result
                from lintro.models.core.tool_result import ToolResult

                failed_result = ToolResult(
                    name=tool_name,
                    success=False,
                    output=f"Parallel execution failed: {result}",
                    issues_count=0,
                )
                processed_results.append((tool_name, failed_result))
            else:
                # Result is tuple[str, ToolResult] (type narrowed by isinstance check)
                processed_results.append(result)  # type: ignore[arg-type]

        return processed_results

    def shutdown(self) -> None:
        """Shutdown the thread pool executor."""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None


def get_parallel_batches(
    tools: list[str],
    tool_manager: Any,
) -> list[list[str]]:
    """Group tools into batches that can run in parallel.

    Tools with conflicts (e.g., Black and Ruff formatter) must run in separate
    batches to avoid race conditions on the same files.

    Args:
        tools: List of tool names to batch.
        tool_manager: Tool manager instance to query tool definitions.

    Returns:
        List of batches, where each batch is a list of tool names that can
        run in parallel.
    """
    if not tools:
        return []

    # Build conflict graph
    conflict_graph: dict[str, set[str]] = {name: set() for name in tools}

    for tool_name in tools:
        try:
            tool_instance = tool_manager.get_tool(tool_name)
            for conflict in tool_instance.definition.conflicts_with:
                conflict_lower = conflict.lower()
                if conflict_lower in tools:
                    conflict_graph[tool_name].add(conflict_lower)
                    conflict_graph[conflict_lower].add(tool_name)
        except (KeyError, AttributeError):
            # Tool not found or has no conflicts
            pass

    # Greedy batching: add tools to current batch if they don't conflict
    # with any tool already in the batch
    batches: list[list[str]] = []
    remaining = set(tools)

    while remaining:
        batch: list[str] = []
        batch_conflicts: set[str] = set()

        for tool_name in tools:  # Iterate in original order for determinism
            if tool_name not in remaining:
                continue

            # Check if this tool conflicts with anything in current batch
            if tool_name not in batch_conflicts:
                batch.append(tool_name)
                remaining.remove(tool_name)
                # Add this tool's conflicts to the set
                batch_conflicts.update(conflict_graph[tool_name])
                batch_conflicts.add(tool_name)

        if batch:
            batches.append(batch)
        else:
            # Safety: if we couldn't add anything, break to avoid infinite loop
            break

    return batches
