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
                        confidence=raw.get(
                            "confidence",
                            ConfidenceLevel.MEDIUM,
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
        return AISummary(
            overview=str(raw_summary.get("overview", "")),
            key_patterns=raw_summary.get("key_patterns", []),
            priority_actions=raw_summary.get("priority_actions", []),
            triage_suggestions=raw_summary.get("triage_suggestions", []),
            estimated_effort=str(raw_summary.get("estimated_effort", "")),
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_estimate=cost,
        )
    return None
