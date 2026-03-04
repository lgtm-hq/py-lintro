"""Parallel tool execution utilities.

This module provides functions for running tools in parallel using async execution.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

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
    from lintro.plugins.base import BaseToolPlugin


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

                    # Configure tool using shared helper
                    configure_tool_for_execution(
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

                # Run batch in parallel with progress callback
                batch_results = asyncio.run(
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
