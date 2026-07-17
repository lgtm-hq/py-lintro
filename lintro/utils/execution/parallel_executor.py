"""Parallel tool execution utilities.

This module provides functions for running tools in parallel using async execution.
"""

from __future__ import annotations

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, TypeVar

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.execution.tool_configuration import configure_tool_for_execution
from lintro.utils.unified_config import UnifiedConfigManager

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from lintro.plugins.base import BaseToolPlugin

_T = TypeVar("_T")


def _run_coroutine_blocking(coro: Coroutine[object, object, _T]) -> _T:
    """Run a coroutine to completion from a synchronous context.

    This helper guarantees the parallel executor can be embedded inside an
    environment that already owns a running event loop (Jupyter, async web
    frameworks, or a programmatic library caller invoking lintro from async
    code). Loop ownership is only safe to assume at the CLI boundary, so the
    executor must never assume it can create its own loop unconditionally.

    Behavior:
        - No running loop in the current thread: the coroutine runs via
          ``asyncio.run`` on this thread. This keeps the CLI path
          byte-for-byte identical to the previous ``asyncio.run(...)`` call.
        - A loop is already running in the current thread: the coroutine is
          dispatched to a dedicated worker thread with its own fresh event
          loop, avoiding the ``RuntimeError`` that ``asyncio.run`` raises when
          a loop is already running.

    Args:
        coro: The coroutine to execute to completion.

    Returns:
        The value returned by the coroutine.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread: it is safe to own one here.
        return asyncio.run(coro)

    # A loop is already running in this thread. Run the coroutine on a fresh
    # loop inside a dedicated worker thread and block for its result.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


def run_tools_parallel(
    tools_to_run: list[str],
    paths: list[str],
    action: Action,
    config_manager: UnifiedConfigManager,
    tool_option_dict: dict[str, dict[str, object]],
    exclude: str | None,
    include_venv: bool,
    post_tools: set[str],
    max_workers: int,
    incremental: bool = False,
    auto_install: bool = False,
    max_fix_retries: int = 3,
    diff_base: str | None = None,
) -> list[ToolResult]:
    """Run tools in parallel using async executor.

    Args:
        tools_to_run: List of tool names to run.
        paths: List of file paths to process.
        action: Action to perform.
        config_manager: Unified config manager.
        tool_option_dict: Parsed tool options from CLI.
        exclude: Exclude patterns.
        include_venv: Whether to include venv.
        post_tools: Set of post-check tool names.
        max_workers: Maximum parallel workers.
        incremental: Whether to only check changed files.
        auto_install: Whether to auto-install Node.js deps if missing.
        max_fix_retries: Maximum fix→verify convergence cycles.
        diff_base: Resolved git base ref for ``--diff`` scanning, or None.

    Returns:
        List of ToolResult objects.
    """
    from loguru import logger

    from lintro.utils.async_tool_executor import (
        AsyncToolExecutor,
        get_parallel_batches,
    )

    # Group tools into batches that can run in parallel
    batches = get_parallel_batches(tools_to_run, tool_manager)
    logger.debug(f"Parallel execution batches: {batches}")

    all_results: list[ToolResult] = []
    executor = AsyncToolExecutor(max_workers=max_workers)
    total_tools = len(tools_to_run)

    # Disable progress when not in a TTY
    disable_progress = not sys.stdout.isatty()

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
            disable=disable_progress,
        ) as progress:
            task = progress.add_task(
                f"Running {total_tools} tools...",
                total=total_tools,
            )
            completed_count = 0

            for batch in batches:
                # Prepare tools in batch
                tools_with_instances: list[tuple[str, BaseToolPlugin]] = []

                for tool_name in batch:
                    tool = tool_manager.get_tool(tool_name)

                    # Configure tool using shared helper. This returns a
                    # private per-invocation copy so concurrent batch
                    # execution never races on the shared singleton's options.
                    tool = configure_tool_for_execution(
                        tool=tool,
                        tool_name=tool_name,
                        config_manager=config_manager,
                        tool_option_dict=tool_option_dict,
                        exclude=exclude,
                        include_venv=include_venv,
                        incremental=incremental,
                        action=action,
                        post_tools=post_tools,
                        auto_install=auto_install,
                        diff_base=diff_base,
                    )

                    tools_with_instances.append((tool_name, tool))

                # Update progress description for this batch
                batch_names = ", ".join(batch)
                progress.update(
                    task,
                    description=f"Running: {batch_names}",
                )

                # Create callback to update progress on completion
                def on_tool_complete(
                    name: str,
                    result: ToolResult,
                ) -> None:
                    """Update progress when a tool completes.

                    Args:
                        name: Name of the completed tool.
                        result: Result from the tool execution.
                    """
                    nonlocal completed_count
                    completed_count += 1
                    status = "✓" if result.success else "✗"
                    desc = f"{status} {name} done ({completed_count}/{total_tools})"
                    progress.update(
                        task,
                        completed=completed_count,
                        description=desc,
                    )

                # Run batch in parallel with progress callback. Use the
                # loop-aware runner so the executor works both from the CLI
                # (no running loop) and when embedded in an already-running
                # event loop.
                batch_results = _run_coroutine_blocking(
                    executor.run_tools_parallel(
                        tools=tools_with_instances,
                        paths=paths,
                        action=action,
                        on_result=on_tool_complete,
                        max_fix_retries=max_fix_retries,
                    ),
                )

                # Collect results
                for _, result in batch_results:
                    all_results.append(result)

    finally:
        executor.shutdown()

    return all_results
