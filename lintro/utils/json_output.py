"""JSON output utilities for Lintro.

This module provides functionality for creating JSON output from tool results.
"""

from typing import Any

from lintro.enums.action import Action, normalize_action
from lintro.models.core.tool_result import ToolResult


def create_json_output(
    action: str | Action,
    results: list[ToolResult],
    total_issues: int,
    total_fixed: int,
    total_remaining: int,
    exit_code: int,
) -> dict[str, Any]:
    """Create JSON output data structure from tool results.

    Args:
        action: The action being performed (check, fmt, test).
        results: List of tool result objects.
        total_issues: Total number of issues found.
        total_fixed: Total number of issues fixed (only for FIX action).
        total_remaining: Total number of issues remaining (only for FIX action).
        exit_code: Exit code for the run.

    Returns:
        Dictionary containing JSON-serializable results and summary data.
    """
    # Normalize action to Action enum if string
    action_enum = normalize_action(action) if isinstance(action, str) else action

    json_data: dict[str, Any] = {
        "results": [],
        "summary": {
            "total_issues": total_issues,
            "total_fixed": total_fixed if action_enum == Action.FIX else 0,
            "total_remaining": total_remaining if action_enum == Action.FIX else 0,
        },
    }
    for result in results:
        result_data: dict[str, Any] = {
            "tool": result.name,
            "success": getattr(result, "success", True),
            "issues_count": getattr(result, "issues_count", 0),
            "skipped": getattr(result, "skipped", False),
            "skip_reason": getattr(result, "skip_reason", None),
        }
        if action_enum == Action.FIX:
            result_data["fixed"] = getattr(result, "fixed_issues_count", 0)
            result_data["remaining"] = getattr(
                result,
                "remaining_issues_count",
                0,
            )

        # Include AI metadata when present
        ai_metadata = getattr(result, "ai_metadata", None)
        if ai_metadata:
            result_data["ai_metadata"] = ai_metadata

        json_data["results"].append(result_data)

    # Extract AI summary from the first result that has one
    for result in results:
        ai_metadata = getattr(result, "ai_metadata", None)
        if ai_metadata and "summary" in ai_metadata:
            json_data["ai_summary"] = ai_metadata["summary"]
            break

    return json_data
