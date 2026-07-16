"""Exit code determination and result aggregation utilities.

This module provides functions for determining exit codes and aggregating
tool results from linting operations.
"""

from __future__ import annotations

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
    main_phase_empty_due_to_filter: bool,
) -> int:
    """Determine final exit code based on results.

    Args:
        action: The action performed (check/fix/test).
        all_results: List of all tool results.
        total_issues: Total issues found.
        total_remaining: Remaining issues after fix.
        main_phase_empty_due_to_filter: Whether main phase was empty due to filtering.

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

    # If all tools were filtered to post-checks but nothing ran, return failure
    if main_phase_empty_due_to_filter and not all_results:
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
