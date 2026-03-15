"""SARIF bridge: reconstruct typed AI objects from ToolResult metadata.

This module provides functions to reconstruct ``AIFixSuggestion`` and
``AISummary`` instances from the serialized metadata dictionaries that
are attached to ``ToolResult.ai_metadata`` during AI-enhanced runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintro.ai.enums import ConfidenceLevel
from lintro.ai.models import AIFixSuggestion, AISummary

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult


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
            suggestions.append(
                AIFixSuggestion(
                    file=raw.get("file", ""),
                    line=int(raw.get("line", 0)),
                    code=raw.get("code", ""),
                    tool_name=raw.get("tool_name", ""),
                    original_code=raw.get("original_code", ""),
                    suggested_code=raw.get("suggested_code", ""),
                    diff=raw.get("diff", ""),
                    explanation=raw.get("explanation", ""),
                    confidence=raw.get(
                        "confidence",
                        ConfidenceLevel.MEDIUM,
                    ),
                    risk_level=raw.get("risk_level", ""),
                    input_tokens=int(raw.get("input_tokens", 0)),
                    output_tokens=int(raw.get("output_tokens", 0)),
                    cost_estimate=float(raw.get("cost_estimate", 0.0)),
                ),
            )
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
        return AISummary(
            overview=raw_summary.get("overview", ""),
            key_patterns=raw_summary.get("key_patterns", []),
            priority_actions=raw_summary.get("priority_actions", []),
            triage_suggestions=raw_summary.get("triage_suggestions", []),
            estimated_effort=raw_summary.get("estimated_effort", ""),
            input_tokens=raw_summary.get("input_tokens", 0),
            output_tokens=raw_summary.get("output_tokens", 0),
            cost_estimate=raw_summary.get("cost_estimate", 0.0),
        )
    return None
