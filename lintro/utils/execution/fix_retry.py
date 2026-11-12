"""Convergence retries for non-idempotent formatters.

Some formatters (e.g. prettier with ``proseWrap``) need multiple
write-then-verify cycles before their output stabilises. This module owns that
retry loop so the executor itself stays free of tool-specific behaviour.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.models.core.issue_identity import union_issues
from lintro.models.core.result_completeness import is_incomplete_result
from lintro.models.core.tool_result import ToolResult
from lintro.utils.execution.result_shaping import get_remaining_count

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

__all__ = ["run_fix_with_retry"]


def _incomplete_reason(result: ToolResult) -> str:
    """Name why a fix pass measured no residual.

    Args:
        result: An incomplete fix pass.

    Returns:
        The producer's own reason when it gave one, otherwise one derived from
        the failure evidence.
    """
    if result.residual_unknown_reason:
        return result.residual_unknown_reason
    if result.parse_failures_count:
        return "tool output could not be fully parsed"
    if result.execution_failed:
        return "a fix pass failed before measuring what remains"
    return "the fix timed out before what remains was measured"


def _mark_incomplete_unknown(result: ToolResult) -> ToolResult:
    """Ensure an incomplete pass carries explicit unknown fix accounting.

    Every return path normalizes through here, the first pass and the
    no-retry path included, so a pass that failed to execute, timed out or
    reported incompletely never keeps counts it did not measure (#3352).

    Args:
        result: Fix result to normalize before returning or merging it.

    Returns:
        A validated replacement for incomplete passes, otherwise the input.
        A pass that already reports unknown accounting is returned as is.
    """
    if not is_incomplete_result(result):
        return result
    if (
        result.residual_unknown
        and not result.success
        and result.fixed_issues_count is None
        and result.remaining_issues_count is None
    ):
        return result
    return replace(
        result,
        success=False,
        fixed_issues_count=None,
        remaining_issues_count=None,
        residual_unknown=True,
        residual_unknown_reason=_incomplete_reason(result),
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
    ToolResult.validate_outcome(result)
    result = _mark_incomplete_unknown(result)

    # An incomplete first pass has no complete baseline to merge or retry.
    if max_retries <= 1 or is_incomplete_result(result):
        return result

    initial_issues_count = result.initial_issues_count
    first_pass_initial_issues = result.initial_issues
    remaining = get_remaining_count(result)

    for attempt in range(2, max_retries + 1):
        # An incomplete report or failed execution is not a convergence
        # measurement. Retrying must not replace its failure evidence with a
        # later clean result (#3352).
        if is_incomplete_result(result):
            break
        if remaining == 0:
            break

        logger.debug(
            f"Fix retry {attempt}/{max_retries} for "
            f"{getattr(getattr(tool, 'definition', None), 'name', 'unknown')} "
            f"({remaining} remaining issues)",
        )
        result = tool.fix(paths, options)
        ToolResult.validate_outcome(result)
        result = _mark_incomplete_unknown(result)
        remaining = get_remaining_count(result)

    # Merge: keep initial_issues_count and initial_issues from first pass,
    # rest from last pass, including failure accounting and execution metadata.
    # Empty collections alone do not establish a measured zero. Producers
    # record that measurement explicitly in initial_issues_count.
    if initial_issues_count is None and first_pass_initial_issues:
        initial_issues_count = len(first_pass_initial_issues)
    if is_incomplete_result(result):
        # The same test that ended the loop: a pass that failed to execute,
        # timed out or reported incompletely measured no residual, so its
        # counts stay unknown rather than falling back to ``issues_count``
        # (#3352). Its findings are genuine even though the pass failed, so
        # they join the originally detected ones; duplicates are matched by
        # finding identity, not object equality.
        issues = union_issues(first_pass_initial_issues, result.issues)
        return replace(
            result,
            issues=issues,
            issues_count=max(len(issues), result.issues_count),
            initial_issues_count=initial_issues_count,
            initial_issues=first_pass_initial_issues,
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
