"""Tool re-execution service for post-fix verification.

Re-runs tools on files modified by AI fixes to get fresh remaining
issue counts. Files are passed as absolute paths so each tool resolves
its own working directory from the target files, matching the directory
used during the original run without mutating the process-global cwd.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.models.core.tool_outcome import FixMeasured, FixPartial, ToolOutcome
from lintro.tools.core.verify_pass import check_answered

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
    """Replace per-tool entries with validated measured rerun results.

    An unanswered check leaves the original counts and failure flags intact.
    Answered checks replace execution state (including a prior timeout) and the
    residual, preserving the native fixed count. AI fixes are reported separately
    through verification metadata. If the counters no longer balance, omit the
    initial count and retain the native/residual measurements as partial accounting.
    Incomplete reports cannot gain complete accounting.

    Args:
        by_tool: Dict mapping tool name to (result, issues) pairs; entries are
            replaced while existing results and grouped issues are preserved.
        rerun_results: Fresh results from re-running tools.
    """
    rerun_by_name = {result.name: result for result in rerun_results}

    for tool_name, (result, _issues) in by_tool.items():
        rerun = rerun_by_name.get(tool_name)
        if rerun is None or not check_answered(rerun) or result.parse_failures_count:
            continue

        try:
            previous = result.outcome.accounting
        except ValueError:
            # An untyped legacy plugin can supply invalid original state.
            # It supplies no trustworthy accounting to refresh.
            logger.warning(
                f"AI post-fix rerun skipped for {tool_name}: invalid original",
            )
            continue

        refreshed_issues = list(rerun.issues)
        remaining = len(refreshed_issues)
        accounting: FixMeasured | FixPartial
        if (
            previous.initial is not None
            and previous.fixed is not None
            and previous.initial == previous.fixed + remaining
        ):
            accounting = FixMeasured(
                initial=previous.initial,
                fixed=previous.fixed,
                remaining=remaining,
            )
        else:
            # Native fixes and a post-AI residual span different phases. Keep
            # both measurements without claiming a complete before/after set
            # or counting AI fixes again as native. A missing native count
            # cannot be reconstructed from the post-AI residual either.
            accounting = FixPartial(
                initial=previous.initial if previous.fixed is None else None,
                fixed=previous.fixed,
                remaining=remaining,
            )
        outcome = ToolOutcome(
            execution=rerun.outcome.execution,
            accounting=accounting,
            issues_count=remaining,
        )
        by_tool[tool_name] = (
            replace(
                result,
                **outcome.to_legacy_fields(),
                issues=refreshed_issues,
                output=rerun.output if rerun.output is not None else result.output,
            ),
            _issues,
        )
