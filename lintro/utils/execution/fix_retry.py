"""Convergence retries for non-idempotent formatters.

Some formatters (e.g. prettier with ``proseWrap``) need multiple
write-then-verify cycles before their output stabilises. This module owns that
retry loop so the executor itself stays free of tool-specific behaviour.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.models.core.tool_result import ToolResult
from lintro.utils.execution.result_shaping import get_remaining_count

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

__all__ = ["run_fix_with_retry"]


def _mark_parse_failure_unknown(result: ToolResult) -> None:
    """Ensure an incomplete report carries explicit unknown fix accounting.

    Args:
        result: Fix result to normalize before returning or merging it.
    """
    if result.parse_failures_count:
        result.success = False
        result.fixed_issues_count = None
        result.remaining_issues_count = None
        result.residual_unknown = True
        result.residual_unknown_reason = (
            result.residual_unknown_reason or "tool output could not be fully parsed"
        )


def run_fix_with_retry(
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
    _mark_parse_failure_unknown(result)

    # An incomplete first pass has no complete baseline to merge or retry.
    if max_retries <= 1 or result.residual_unknown or result.parse_failures_count:
        return result

    initial_issues_count = result.initial_issues_count
    first_pass_initial_issues = result.initial_issues
    remaining = get_remaining_count(result)

    for attempt in range(2, max_retries + 1):
        # An incomplete report is not a convergence measurement. Retrying
        # must not replace its failure evidence with a later clean result.
        if result.residual_unknown or result.parse_failures_count:
            break
        if remaining == 0:
            break

        logger.debug(
            f"Fix retry {attempt}/{max_retries} for "
            f"{getattr(getattr(tool, 'definition', None), 'name', 'unknown')} "
            f"({remaining} remaining issues)",
        )
        result = tool.fix(paths, options)
        _mark_parse_failure_unknown(result)
        remaining = get_remaining_count(result)

    # Merge: keep initial_issues_count and initial_issues from first pass,
    # rest from last pass, including failure accounting and execution metadata.
    # Empty collections alone do not establish a measured zero. Producers
    # record that measurement explicitly in initial_issues_count.
    if initial_issues_count is None and first_pass_initial_issues:
        initial_issues_count = len(first_pass_initial_issues)
    if result.residual_unknown or result.parse_failures_count:
        # Execution failures carry the original detected findings. Parse
        # failures also retain the latest report's usable partial findings.
        # An empty original list must not discard findings from a later pass,
        # even when the first pass explicitly measured zero.
        issues = (
            first_pass_initial_issues
            if not result.parse_failures_count and first_pass_initial_issues
            else result.issues
        )
        return replace(
            result,
            issues=issues,
            issues_count=max(len(issues), result.issues_count),
            initial_issues_count=initial_issues_count,
            initial_issues=first_pass_initial_issues,
            fixed_issues_count=None,
            remaining_issues_count=None,
        )
    if initial_issues_count is not None:
        result = replace(
            result,
            issues_count=remaining,
            initial_issues_count=initial_issues_count,
            fixed_issues_count=max(0, initial_issues_count - remaining),
            remaining_issues_count=remaining,
            initial_issues=first_pass_initial_issues,
        )

    return result
