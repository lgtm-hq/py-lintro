"""Parallel tool execution utilities.

This module provides functions for running tools in parallel using async
execution.

Mutation is the exception. Under a mutating action (``Action.FIX``) the
batches still run in derived DAG order, but each batch dispatches one tool at
a time: two mutators that overlap on a file would race on its bytes, and the
loser's write is simply gone. Read-only actions — ``check``, and the
run-level verify pass, which is configured with ``Action.CHECK`` — keep the
full fan-out.
"""

from __future__ import annotations

import asyncio
import sys
import time
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
    selected_tools: set[str],
    max_workers: int,
    incremental: bool = False,
    auto_install: bool = False,
    max_fix_retries: int = 3,
    diff_base: str | None = None,
) -> list[ToolResult]:
    """Run tools through the async executor, batch by batch.

    Under a read-only action every tool in a batch runs concurrently. Under a
    mutating action the batch is dispatched one tool at a time, so no two
    mutating capabilities are ever in flight at once (#1743).

    Args:
        tools_to_run: List of tool names to run.
        paths: List of file paths to process.
        action: Action to perform.
        config_manager: Unified config manager.
        tool_option_dict: Parsed tool options from CLI.
        exclude: Exclude patterns.
        include_venv: Whether to include venv.
        selected_tools: Every tool selected for this run, used to
            resolve per-pattern format authority.
        max_workers: Maximum parallel workers.
        incremental: Whether to only check changed files.
        auto_install: Whether to auto-install Node.js deps if missing.
        max_fix_retries: Maximum fix→verify convergence cycles.
        diff_base: Resolved git base ref for ``--diff`` scanning, or None.

    Returns:
        List of ToolResult objects.
    """
    from loguru import logger

    from lintro.utils.async_tool_executor import AsyncToolExecutor

    # Group tools into batches that can run in parallel. The batching lives on
    # the tool manager because it reads the same derived DAG that orders a
    # sequential run (#1742).
    batches = tool_manager.get_parallel_batches(tools_to_run)
    logger.debug(f"Parallel execution batches: {batches}")

    # The mutation phase runs one tool at a time. Two mutating capabilities
    # that overlap on a file race on its bytes: each reads, rewrites and writes
    # the whole file, so the second write drops the first tool's edit and the
    # verify pass reports the difference as an unexplained residual. Reading
    # is safe, so ``check`` runs — and so does the verify pass, which is
    # configured with ``Action.CHECK`` — stay fully parallel. This is a bridge
    # until the scheduler gains an overlap rule that can keep disjoint
    # mutators concurrent.
    serialize_mutations = action == Action.FIX
    if serialize_mutations:
        logger.debug("Mutating action: batches run one tool at a time")

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
                    # A tool that cannot be resolved or configured becomes a
                    # failed result, exactly as it does in the sequential
                    # path. Before #1742 post-check filtering usually left one
                    # tool in the main list, so this branch ran sequentially
                    # and an unresolvable tool never reached here; now every
                    # selected tool stays in one list and this path is live.
                    attempt_started = time.monotonic()
                    try:
                        tool = tool_manager.get_tool(tool_name)

                        # Configure tool using shared helper. This returns a
                        # private per-invocation copy so concurrent batch
                        # execution never races on the shared singleton's
                        # options.
                        tool = configure_tool_for_execution(
                            tool=tool,
                            tool_name=tool_name,
                            config_manager=config_manager,
                            tool_option_dict=tool_option_dict,
                            exclude=exclude,
                            include_venv=include_venv,
                            incremental=incremental,
                            action=action,
                            selected_tools=selected_tools,
                            auto_install=auto_install,
                            diff_base=diff_base,
                        )
                    except (OSError, ValueError, RuntimeError) as exc:
                        # Same telemetry the sequential path records: a
                        # console line so the failure is visible, and a
                        # duration so a crashed tool still appears in
                        # ``--profile``. The line goes to stderr because this
                        # function has no ``RunContext`` logger to ask about
                        # the output format, and stdout may be carrying JSON
                        # or SARIF.
                        logger.exception(f"Error running {tool_name}")
                        print(
                            f"Error running {tool_name}: {exc}",
                            file=sys.stderr,
                        )
                        all_results.append(
                            ToolResult(
                                name=tool_name,
                                success=False,
                                output=f"Failed to initialize tool: {exc}",
                                issues_count=0,
                                duration_seconds=time.monotonic() - attempt_started,
                            ),
                        )
                        completed_count += 1
                        progress.update(task, completed=completed_count)
                        continue

                    tools_with_instances.append((tool_name, tool))

                if not tools_with_instances:
                    # Every tool in the batch failed to initialize.
                    continue

                # Update progress description for this batch
                batch_names = ", ".join(name for name, _ in tools_with_instances)
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

                # Run the batch with the progress callback. Use the loop-aware
                # runner so the executor works both from the CLI (no running
                # loop) and when embedded in an already-running event loop.
                # A mutating action dispatches one tool per group, so no two
                # mutators are ever in flight over the same file at once; a
                # read-only action dispatches the whole batch as one group.
                groups: list[list[tuple[str, BaseToolPlugin]]] = (
                    [[pair] for pair in tools_with_instances]
                    if serialize_mutations
                    else [tools_with_instances]
                )
                for group in groups:
                    group_results = _run_coroutine_blocking(
                        executor.run_tools_parallel(
                            tools=group,
                            paths=paths,
                            action=action,
                            on_result=on_tool_complete,
                            max_fix_retries=max_fix_retries,
                        ),
                    )

                    # Collect results
                    for _, result in group_results:
                        all_results.append(result)

    finally:
        executor.shutdown()

    return all_results
