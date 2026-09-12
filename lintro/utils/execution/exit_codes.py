"""Exit code determination and result aggregation utilities.

This module provides functions for determining exit codes and aggregating
tool results from linting operations.
"""

from __future__ import annotations

from collections.abc import Sequence

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult

# Constants
DEFAULT_EXIT_CODE_SUCCESS: int = 0
DEFAULT_EXIT_CODE_FAILURE: int = 1
DEFAULT_REMAINING_COUNT: str = "?"


def determine_exit_code(
    action: Action,
    all_results: list[ToolResult],
    total_issues: int,
    total_remaining: int,
) -> int:
    """Determine final exit code based on results.

    Args:
        action: The action performed (check/fix/test).
        all_results: List of all tool results.
        total_issues: Total issues found.
        total_remaining: Remaining issues after fix.

    Returns:
        Exit code (0=success, 1=failure).
    """
    exit_code = DEFAULT_EXIT_CODE_SUCCESS

    # Check for tool failures first (applies to all actions)
    # Exclude skipped tools — they didn't fail, they just didn't run
    if any(
        not getattr(r, "success", True)
        for r in all_results
        if not getattr(r, "skipped", False)
    ):
        exit_code = DEFAULT_EXIT_CODE_FAILURE

    # Then check for issues based on action
    if action == Action.FIX:
        if total_remaining > 0:
            exit_code = DEFAULT_EXIT_CODE_FAILURE
    else:  # check
        if total_issues > 0:
            exit_code = DEFAULT_EXIT_CODE_FAILURE

    return exit_code


def aggregate_tool_results(
    results: list[ToolResult],
    action: Action,
) -> tuple[int, int, int]:
    """Aggregate results and compute totals.

    Args:
        results: List of tool results to aggregate.
        action: The action performed (determines which counts to aggregate).

    Returns:
        Tuple of (total_issues, total_fixed, total_remaining). In CHECK/TEST
        mode nothing is fixed, so ``total_remaining`` mirrors ``total_issues``
        rather than the misleading constant 0.

        In FIX mode the two derived totals cover only the tools whose residual
        the verify pass measured. A tool in the third state — residual unknown
        (#1743) — contributes nothing to either, because adding a zero for it
        would present an after-count nobody took. Its *pre-fix* findings still
        reach ``total_issues``: those were measured. Callers that render the
        totals must say how many tools were left out; see
        :func:`unknown_residual_tool_names`.
    """
    total_issues = 0
    total_fixed = 0
    total_remaining = 0

    for result in results:
        # Exclude skipped tools from totals
        if getattr(result, "skipped", False):
            continue
        total_issues += getattr(result, "issues_count", 0)

        if action == Action.FIX:
            if getattr(result, "residual_unknown", False):
                # No measurement exists for this tool. Deliberately not a
                # zero: the run reports it as unknown instead.
                continue
            fixed = getattr(result, "fixed_issues_count", None)
            total_fixed += fixed if fixed is not None else 0
            remaining = getattr(result, "remaining_issues_count", None)
            total_remaining += remaining if remaining is not None else 0

    # Outside FIX mode nothing is fixed; the remaining count is simply the
    # total issues found. Mirroring it here keeps the check-mode JSON summary
    # from reporting "total_remaining": 0 alongside a nonzero "total_issues".
    if action != Action.FIX:
        total_remaining = total_issues

    return total_issues, total_fixed, total_remaining


def unknown_residual_tool_names(results: Sequence[object]) -> list[str]:
    """Return the tools whose residual the verify pass could not measure.

    The run totals leave these tools out rather than folding a zero in for
    them (#1743), so every surface that renders those totals needs the list to
    say what the numbers do not cover. Without it the TOTALS table reads as a
    measured "0 remaining" for a tool whose own row says "unknown".

    Args:
        results: Every result in the run, in execution order. Typed loosely
            because the console layer holds them as opaque objects.

    Returns:
        list[str]: Names of the tools in the third state, in run order.
    """
    return [
        str(getattr(result, "name", ""))
        for result in results
        if getattr(result, "residual_unknown", False)
    ]
