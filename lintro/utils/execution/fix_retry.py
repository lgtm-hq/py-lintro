"""Convergence retries for non-idempotent formatters.

Some formatters (e.g. prettier with ``proseWrap``) need multiple
write-then-verify cycles before their output stabilises. This module owns that
retry loop so the executor itself stays free of tool-specific behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.models.core.tool_result import ToolResult
from lintro.utils.execution.result_shaping import get_remaining_count

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


def _run_fix_with_retry(
    tool: BaseToolPlugin,
    paths: list[str],
    options: dict[str, object],
    max_retries: int,
) -> ToolResult:
    """Run ``tool.fix()`` with convergence retries.

    Retries ``fix()`` up to ``max_retries`` times, keeping the initial issue
    count from the first pass and the remaining count from the last pass.

    Args:
        tool: The tool plugin to execute.
        paths: List of file paths to process.
        options: Runtime options for the tool.
        max_retries: Maximum number of fix-then-verify cycles.

    Returns:
        ToolResult: Merged result across all passes.
    """
    from loguru import logger

    result = tool.fix(paths, options)

    if max_retries <= 1:
        return result

    initial_issues_count = getattr(result, "initial_issues_count", None)
    first_pass_initial_issues = getattr(result, "initial_issues", None)
    remaining = get_remaining_count(result)

    for attempt in range(2, max_retries + 1):
        if remaining == 0:
            break

        logger.debug(
            f"Fix retry {attempt}/{max_retries} for "
            f"{getattr(getattr(tool, 'definition', None), 'name', 'unknown')} "
            f"({remaining} remaining issues)",
        )
        result = tool.fix(paths, options)
        remaining = get_remaining_count(result)

    # Merge: keep initial_issues_count and initial_issues from first pass,
    # rest from last pass. ``timed_out`` must be carried over from the final
    # pass: rebuilding the result field-by-field would otherwise erase it, and
    # a tool that really did time out would serialize ``timed_out: false``.
    if initial_issues_count is not None:
        fixed = max(0, initial_issues_count - remaining)
        result = ToolResult(
            name=result.name,
            success=result.success,
            output=result.output,
            issues_count=remaining,
            issues=result.issues,
            initial_issues_count=initial_issues_count,
            fixed_issues_count=fixed,
            remaining_issues_count=remaining,
            formatted_output=result.formatted_output,
            initial_issues=first_pass_initial_issues,
            cwd=result.cwd,
            timed_out=result.timed_out,
        )
    elif first_pass_initial_issues is not None:
        # Preserve initial_issues even when initial_issues_count is absent
        fixed = max(0, len(first_pass_initial_issues) - remaining)
        result = ToolResult(
            name=result.name,
            success=result.success,
            output=result.output,
            issues_count=remaining,
            issues=result.issues,
            initial_issues_count=len(first_pass_initial_issues),
            fixed_issues_count=fixed,
            remaining_issues_count=remaining,
            formatted_output=result.formatted_output,
            initial_issues=first_pass_initial_issues,
            cwd=result.cwd,
            timed_out=result.timed_out,
        )

    return result
