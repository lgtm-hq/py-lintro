"""Tool re-execution service for post-fix verification.

Re-runs tools on files modified by AI fixes to get fresh remaining
issue counts, using the original tool execution cwd for consistency.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Iterator

    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue

_rerun_cwd_lock = threading.Lock()


@contextmanager
def _tool_cwd(cwd: str | None) -> Iterator[None]:
    """Context manager for tool execution in a specific cwd.

    Uses a process-global lock because ``os.chdir`` is process-wide
    and tools call ``subprocess.run`` internally without a ``cwd`` param.

    Args:
        cwd: Directory to chdir into, or None to skip.
    """
    if not cwd:
        yield
        return

    with _rerun_cwd_lock:
        original_cwd = Path.cwd()
        os.chdir(cwd)
        try:
            yield
        finally:
            os.chdir(original_cwd)


def paths_for_context(
    *,
    file_paths: list[str],
    cwd: str | None,
) -> list[str]:
    """Prefer paths relative to tool cwd when possible.

    Args:
        file_paths: Absolute file paths to relativize.
        cwd: Tool's original working directory.

    Returns:
        List of paths, made relative to cwd where possible.
    """
    if not cwd:
        return file_paths

    try:
        cwd_path = Path(cwd).resolve()
    except OSError:
        return file_paths

    contextual_paths: list[str] = []
    for file_path in file_paths:
        try:
            resolved = Path(file_path).resolve()
        except OSError:
            contextual_paths.append(file_path)
            continue
        try:
            contextual_paths.append(str(resolved.relative_to(cwd_path)))
        except ValueError:
            contextual_paths.append(str(resolved))
    return contextual_paths


def rerun_tools(
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]],
) -> list[ToolResult] | None:
    """Re-run tools on analyzed files to get fresh remaining issue counts.

    Reuses the original tool execution cwd for path/config consistency.

    Args:
        by_tool: Dict mapping tool name to (result, issues) pairs.

    Returns:
        List of fresh tool results from re-running checks.
    """
    try:
        from lintro.tools import tool_manager
    except ImportError:
        return None

    rerun_results: list[ToolResult] = []
    for tool_name, (result, issues) in by_tool.items():
        file_paths = sorted({issue.file for issue in issues if issue.file})
        if not file_paths:
            continue

        rerun_paths = paths_for_context(file_paths=file_paths, cwd=result.cwd)

        try:
            tool = tool_manager.get_tool(tool_name)
            with _tool_cwd(result.cwd):
                rerun_results.append(tool.check(rerun_paths, {}))
        except Exception:
            logger.debug(
                f"AI post-fix rerun failed for {tool_name}",
                exc_info=True,
            )
            continue
    return rerun_results


def apply_rerun_results(
    *,
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]],
    rerun_results: list[ToolResult],
) -> None:
    """Apply fresh rerun issue counts back to original FIX results.

    Args:
        by_tool: Dict mapping tool name to (result, issues) pairs.
        rerun_results: Fresh results from re-running tools.
    """
    rerun_by_name = {result.name: result for result in rerun_results}

    for tool_name, (result, _issues) in by_tool.items():
        rerun = rerun_by_name.get(tool_name)
        if rerun is None:
            continue

        refreshed_issues = list(rerun.issues) if rerun.issues is not None else []
        result.issues = refreshed_issues
        result.issues_count = len(refreshed_issues)
        result.remaining_issues_count = len(refreshed_issues)
        result.success = rerun.success
        if rerun.output is not None:
            result.output = rerun.output
