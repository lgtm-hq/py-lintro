"""SARIF bridge: reconstruct typed AI objects from ToolResult metadata.

This module provides functions to reconstruct ``AIFixSuggestion`` and
``AISummary`` instances from the serialized metadata dictionaries that
are attached to ``ToolResult.ai_metadata`` during AI-enhanced runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintro.ai.enums import ConfidenceLevel
from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.ai.output.sarif import StandardIssue
from lintro.enums.severity_level import SeverityLevel

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue


def standard_issues_from_results(
    all_results: list[ToolResult],
) -> list[StandardIssue]:
    """Extract normalized standard lint issues from ToolResults.

    Reads ``result.issues`` directly (independent of AI metadata) and
    normalizes each ``BaseIssue`` into a ``StandardIssue`` carrying the
    fields required for SARIF standard-mode emission.

    Args:
        all_results: List of tool results carrying parsed lint issues.

    Returns:
        List of normalized standard issues across all results.
    """
    standard_issues: list[StandardIssue] = []
    for result in all_results:
        issues = getattr(result, "issues", None)
        if not issues:
            continue
        tool_name = str(getattr(result, "name", "") or "")
        for issue in issues:
            standard_issues.append(
                _to_standard_issue(issue, tool_name=tool_name),
            )
    return standard_issues


def _to_standard_issue(
    issue: BaseIssue,
    *,
    tool_name: str,
) -> StandardIssue:
    """Normalize a single ``BaseIssue`` into a ``StandardIssue``.

    Args:
        issue: Parsed lint issue to normalize.
        tool_name: Name of the tool that produced the issue.

    Returns:
        Normalized standard issue.
    """
    row = issue.to_display_row()
    try:
        severity = issue.get_severity()
    except (ValueError, AttributeError):
        severity = SeverityLevel.WARNING
    return StandardIssue(
        tool_name=tool_name,
        file=str(getattr(issue, "file", "") or ""),
        line=int(getattr(issue, "line", 0) or 0),
        column=int(getattr(issue, "column", 0) or 0),
        code=str(row.get("code", "") or ""),
        message=str(row.get("message", "") or ""),
        severity=severity,
        doc_url=str(
            getattr(issue, "doc_url", "") or getattr(issue, "url", "") or "",
        ),
    )


def _coerce_confidence(value: object) -> ConfidenceLevel:
    """Coerce a raw confidence value to the ``ConfidenceLevel`` enum.

    Accepts enum members, their string names (case-insensitive), or
    falls back to ``MEDIUM`` for unrecognised values.
    """
    if isinstance(value, ConfidenceLevel):
        return value
    if isinstance(value, str):
        try:
            return ConfidenceLevel(value.lower())
        except ValueError:
            return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.MEDIUM


def suggestions_from_results(
    all_results: list[ToolResult],
) -> list[AIFixSuggestion]:
    """Reconstruct AIFixSuggestion objects from ToolResult AI metadata.

    Args:
        all_results: List of tool results potentially carrying AI metadata.

    Returns:
        List of reconstructed AIFixSuggestion objects across all results.
    """
    suggestions: list[AIFixSuggestion] = []
    for result in all_results:
        if result.ai_metadata is None:
            continue
        raw_suggestions = result.ai_metadata.get("fix_suggestions", [])
        if not isinstance(raw_suggestions, list):
            continue
        for raw in raw_suggestions:
            if not isinstance(raw, dict):
                continue
            try:
                suggestions.append(
                    AIFixSuggestion(
                        file=str(raw.get("file", "")),
                        line=int(raw.get("line", 0)),
                        code=str(raw.get("code", "")),
                        tool_name=str(raw.get("tool_name", "")),
                        original_code=str(raw.get("original_code", "")),
                        suggested_code=str(raw.get("suggested_code", "")),
                        diff=str(raw.get("diff", "")),
                        explanation=str(raw.get("explanation", "")),
                        confidence=_coerce_confidence(
                            raw.get("confidence", ConfidenceLevel.MEDIUM),
                        ),
                        risk_level=str(raw.get("risk_level", "")),
                        input_tokens=int(raw.get("input_tokens", 0)),
                        output_tokens=int(raw.get("output_tokens", 0)),
                        cost_estimate=float(
                            raw.get("cost_estimate", 0.0),
                        ),
                    ),
                )
            except (TypeError, ValueError):
                continue
    return suggestions


def summary_from_results(
    all_results: list[ToolResult],
) -> AISummary | None:
    """Reconstruct an AISummary from the first ToolResult that carries one.

    Args:
        all_results: List of tool results potentially carrying AI metadata.

    Returns:
        Reconstructed AISummary, or None if no summary metadata is found.
    """
    for result in all_results:
        if result.ai_metadata is None:
            continue
        raw_summary: dict[str, Any] | None = result.ai_metadata.get("summary")
        if not isinstance(raw_summary, dict):
            continue
        try:
            in_tok = int(raw_summary.get("input_tokens", 0))
        except (TypeError, ValueError):
            in_tok = 0
        try:
            out_tok = int(raw_summary.get("output_tokens", 0))
        except (TypeError, ValueError):
            out_tok = 0
        try:
            cost = float(raw_summary.get("cost_estimate", 0.0))
        except (TypeError, ValueError):
            cost = 0.0

        def _str_list(val: object) -> list[str]:
            if isinstance(val, list):
                return [str(x) for x in val]
            if val is None:
                return []
            return [str(val)]

        return AISummary(
            overview=str(raw_summary.get("overview", "")),
            key_patterns=_str_list(raw_summary.get("key_patterns")),
            priority_actions=_str_list(raw_summary.get("priority_actions")),
            triage_suggestions=_str_list(raw_summary.get("triage_suggestions")),
            estimated_effort=str(raw_summary.get("estimated_effort", "")),
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_estimate=cost,
        )
    return None
