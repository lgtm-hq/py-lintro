"""Tool re-execution service for post-fix verification.

Re-runs tools on files modified by AI fixes to get fresh remaining
issue counts. Files are passed as absolute paths so each tool resolves
its own working directory from the target files, matching the directory
used during the original run without mutating the process-global cwd.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue


def absolute_paths_for_context(
    *,
    file_paths: list[str],
) -> list[str]:
    """Resolve file paths to absolute form for cwd-explicit rerun.

    Passing absolute paths lets each tool derive its subprocess working
    directory from the common parent of the targets, which resolves the
    same directory the original run used without relying on ``os.chdir``.

    Args:
        file_paths: File paths to resolve. May be absolute or relative.

    Returns:
        List of absolute path strings. Unresolvable paths are returned
        unchanged so the tool can surface its own error.
    """
    resolved_paths: list[str] = []
    for file_path in file_paths:
        try:
            resolved_paths.append(str(Path(file_path).resolve()))
        except OSError:
            resolved_paths.append(file_path)
    return resolved_paths


def rerun_tools(
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]],
) -> list[ToolResult] | None:
    """Re-run tools on analyzed files to get fresh remaining issue counts.

    Each tool is re-run against absolute file paths so it resolves the same
    working directory (and therefore the same config) as the original run,
    without mutating the process-global cwd.

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
    for tool_name, (_result, issues) in by_tool.items():
        file_paths = sorted({issue.file for issue in issues if issue.file})
        if not file_paths:
            continue

        rerun_paths = absolute_paths_for_context(file_paths=file_paths)

        try:
            tool = tool_manager.get_tool(tool_name)
            rerun_results.append(tool.check(rerun_paths, {}))
        except (KeyError, ImportError):
            logger.debug(
                f"AI post-fix rerun skipped for {tool_name}: tool not available",
            )
            continue
        except Exception:
            logger.warning(
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
        # Preserve native fix counters — only update remaining issues
        # and issue list. The initial/fixed counts reflect what the native
        # tool originally reported and should not be zeroed.
        result.issues = refreshed_issues
        result.issues_count = len(refreshed_issues)
        result.remaining_issues_count = len(refreshed_issues)
        result.success = rerun.success
        if rerun.output is not None:
            result.output = rerun.output
