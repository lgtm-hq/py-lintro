"""JSON output utilities for Lintro.

This module provides functionality for creating JSON output from tool results.

It exposes a single source of truth for the per-tool JSON object shared by
both serialization paths:

- ``create_json_output`` renders the ``--output-format json`` stdout payload.
- ``lintro.utils.output.file_writer.write_output_file`` renders the
  ``--output <file>`` artifact.

Both build each per-tool object via :func:`serialize_tool_result` so their
schemas cannot silently drift.
"""

from typing import TYPE_CHECKING, Any

from lintro.ai.metadata import normalize_ai_metadata
from lintro.enums.action import Action, normalize_action
from lintro.formatters.formatter import merge_detected_and_remaining
from lintro.models.core.tool_result import ToolResult

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue


def serialize_issue(issue: "BaseIssue") -> dict[str, Any]:
    """Serialize a ``BaseIssue`` to a JSON-safe dictionary.

    Args:
        issue: The issue to serialize.

    Returns:
        Serialized issue data with ``file``, ``line``, ``code`` and
        ``message`` always present and ``doc_url`` included when set.
    """
    data: dict[str, Any] = {
        "file": getattr(issue, "file", "") or "",
        "line": getattr(issue, "line", None) or 0,
        "code": issue.get_code(),
        "message": getattr(issue, "message", "") or "",
    }
    doc_url = getattr(issue, "doc_url", "") or ""
    if doc_url:
        data["doc_url"] = doc_url
    return data


def serialize_tool_result(
    result: ToolResult,
    *,
    action: Action,
) -> dict[str, Any]:
    """Serialize a single ``ToolResult`` into the shared per-tool JSON object.

    This is the single source of truth for the per-tool schema emitted by both
    the stdout payload (:func:`create_json_output`) and the file artifact
    (``write_output_file``). Keeping both callers on this helper prevents the
    two serializers from drifting.

    The ``issues_count`` is derived from the deduplicated merge of pre-fix and
    remaining issues (``len(merged_issues)``) so it stays consistent with the
    ``issues`` array in fix mode, where ``merge_detected_and_remaining``
    collapses duplicate detected/remaining entries.

    Args:
        result: The tool result to serialize.
        action: The action being performed (check, fmt, test).

    Returns:
        The per-tool JSON object: always ``tool``, ``success``,
        ``issues_count``, ``skipped``, ``skip_reason`` and ``output``; plus
        ``parse_failures_count`` when set, ``fixed``/``remaining`` in FIX mode,
        normalized ``ai_metadata`` when present, and ``issues`` when any exist.
    """
    merged_issues = merge_detected_and_remaining(
        getattr(result, "initial_issues", None),
        getattr(result, "issues", None),
    )
    data: dict[str, Any] = {
        "tool": result.name,
        "success": getattr(result, "success", True),
        "issues_count": len(merged_issues),
        "skipped": getattr(result, "skipped", False),
        "skip_reason": getattr(result, "skip_reason", None),
        "output": getattr(result, "output", ""),
    }
    if result.parse_failures_count is not None:
        data["parse_failures_count"] = result.parse_failures_count
    if action == Action.FIX:
        fixed = getattr(result, "fixed_issues_count", None)
        remaining = getattr(result, "remaining_issues_count", None)
        data["fixed"] = fixed if fixed is not None else 0
        data["remaining"] = remaining if remaining is not None else 0
    ai_metadata = getattr(result, "ai_metadata", None)
    if isinstance(ai_metadata, dict) and ai_metadata:
        normalized_ai_metadata = normalize_ai_metadata(ai_metadata)
        if normalized_ai_metadata:
            data["ai_metadata"] = normalized_ai_metadata
    if merged_issues:
        data["issues"] = [serialize_issue(issue) for issue in merged_issues]
    return data


def create_json_output(
    action: str | Action,
    results: list[ToolResult],
    total_issues: int,
    total_fixed: int,
    total_remaining: int,
    exit_code: int,
    health_score: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create JSON output data structure from tool results.

    Args:
        action: The action being performed (check, fmt, test).
        results: List of tool result objects.
        total_issues: Total number of issues found.
        total_fixed: Total number of issues fixed (only for FIX action).
        total_remaining: Total number of issues remaining. In FIX mode this is
            the post-fix remaining count; in CHECK/TEST mode nothing is fixed,
            so it mirrors ``total_issues``.
        exit_code: Exit code for the run.
        health_score: Optional serialized health score dictionary. When
            provided it is added additively under ``summary.health_score``
            without altering any existing keys.

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
            # In CHECK/TEST mode nothing is fixed, so remaining mirrors the
            # total issues rather than the misleading constant 0.
            "total_remaining": (
                total_remaining if action_enum == Action.FIX else total_issues
            ),
        },
    }
    # Additive: include the health score under summary when supplied so the
    # existing schema keys remain untouched.
    if health_score is not None:
        json_data["summary"]["health_score"] = health_score
    for result in results:
        result_data = serialize_tool_result(result, action=action_enum)
        # Extract AI summary from the first result that has one.
        ai_metadata = result_data.get("ai_metadata")
        if (
            isinstance(ai_metadata, dict)
            and "summary" in ai_metadata
            and "ai_summary" not in json_data
        ):
            json_data["ai_summary"] = ai_metadata["summary"]
        json_data["results"].append(result_data)

    return json_data
