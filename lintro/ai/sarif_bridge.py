"""Reconstruct AI objects from tool metadata for SARIF enrichment.

``suggestions_from_results`` and ``summary_from_results`` rebuild
:class:`~lintro.ai.models.AIFixSuggestion` and
:class:`~lintro.ai.models.AISummary` instances from the serialized dicts the
AI layer leaves on :attr:`ToolResult.metadata`. They live in the AI layer
because they genuinely need :mod:`lintro.ai.models`; core reaches them only
through the injected
:class:`~lintro.models.core.ai_seam.AISarifEnricher` seam, which keeps
:mod:`lintro.utils.tool_executor` and :mod:`lintro.utils.output` free of AI
imports (issue #724).

The core-side counterpart, ``standard_issues_from_results``, stays in
:mod:`lintro.utils.output.sarif.bridge`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.enums.confidence_level import ConfidenceLevel

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult

__all__ = [
    "suggestions_from_results",
    "summary_from_results",
]


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
        if result.metadata is None:
            continue
        raw_suggestions = result.metadata.get("fix_suggestions", [])
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
        if result.metadata is None:
            continue
        raw_summary: dict[str, Any] | None = result.metadata.get("summary")
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
