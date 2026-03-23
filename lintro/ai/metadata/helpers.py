"""Helper functions for attaching and normalizing AI metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintro.ai.metadata.fix_suggestion_payload import AIFixSuggestionPayload
from lintro.ai.metadata.summary_payload import AISummaryPayload

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion, AISummary
    from lintro.ai.telemetry import AITelemetry
    from lintro.models.core.tool_result import ToolResult


def summary_to_payload(summary: AISummary) -> AISummaryPayload:
    """Convert AISummary model to JSON-serializable metadata payload."""
    return AISummaryPayload(
        overview=summary.overview,
        key_patterns=summary.key_patterns,
        priority_actions=summary.priority_actions,
        triage_suggestions=summary.triage_suggestions,
        estimated_effort=summary.estimated_effort,
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
        cost_estimate=summary.cost_estimate,
    )


def suggestion_to_payload(
    suggestion: AIFixSuggestion,
) -> AIFixSuggestionPayload:
    """Convert AIFixSuggestion model to JSON-serializable payload."""
    return AIFixSuggestionPayload(
        file=suggestion.file,
        line=suggestion.line,
        code=suggestion.code,
        tool_name=suggestion.tool_name,
        original_code=suggestion.original_code,
        suggested_code=suggestion.suggested_code,
        explanation=suggestion.explanation,
        confidence=suggestion.confidence,
        risk_level=suggestion.risk_level,
        diff=suggestion.diff,
        input_tokens=suggestion.input_tokens,
        output_tokens=suggestion.output_tokens,
        cost_estimate=suggestion.cost_estimate,
    )


def ensure_ai_metadata(result: ToolResult) -> dict[str, Any]:
    """Ensure a ToolResult has a mutable AI metadata container."""
    if result.ai_metadata is None:
        result.ai_metadata = {}
    return result.ai_metadata


def attach_summary_metadata(
    result: ToolResult,
    summary: AISummary,
) -> None:
    """Attach summary metadata without overwriting other AI metadata."""
    metadata = ensure_ai_metadata(result)
    payload = summary_to_payload(summary)
    metadata["summary"] = payload.to_dict()


def attach_fix_suggestions_metadata(
    result: ToolResult,
    suggestions: list[AIFixSuggestion],
) -> None:
    """Attach fix suggestion metadata without overwriting summary."""
    metadata = ensure_ai_metadata(result)
    existing = list(metadata.get("fix_suggestions", []))
    existing.extend(suggestion_to_payload(s).to_dict() for s in suggestions)
    metadata["fix_suggestions"] = existing


def attach_fixed_count_metadata(
    result: ToolResult,
    fixed_count: int,
) -> None:
    """Attach per-tool AI-applied fix count for summary rendering."""
    metadata = ensure_ai_metadata(result)
    applied_count = max(0, int(fixed_count))
    metadata["fixed_count"] = applied_count
    metadata["applied_count"] = applied_count


def attach_validation_counts_metadata(
    result: ToolResult,
    *,
    verified_count: int,
    unverified_count: int,
) -> None:
    """Attach per-tool validation counts for AI-applied fixes."""
    metadata = ensure_ai_metadata(result)
    metadata["verified_count"] = max(0, int(verified_count))
    metadata["unverified_count"] = max(0, int(unverified_count))


def attach_telemetry_metadata(
    results: list[ToolResult],
    telemetry: AITelemetry,
) -> None:
    """Attach telemetry metrics to the first result's AI metadata."""
    if not results:
        return
    metadata = ensure_ai_metadata(results[0])
    metadata["ai_metrics"] = telemetry.to_dict()


def normalize_ai_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy and current AI metadata into one stable shape."""
    normalized: dict[str, Any] = {}

    summary = raw.get("summary")
    if isinstance(summary, dict):
        normalized["summary"] = summary

    fix_suggestions = raw.get("fix_suggestions")
    if fix_suggestions is None:
        fix_suggestions = raw.get("suggestions")
    if isinstance(fix_suggestions, list):
        normalized["fix_suggestions"] = [
            item for item in fix_suggestions if isinstance(item, dict)
        ]

    fixed_count = raw.get("fixed_count")
    if isinstance(fixed_count, int):
        normalized["fixed_count"] = fixed_count

    applied_count = raw.get("applied_count")
    if isinstance(applied_count, int):
        normalized["applied_count"] = applied_count
    elif isinstance(fixed_count, int):
        normalized["applied_count"] = fixed_count

    verified_count = raw.get("verified_count")
    if isinstance(verified_count, int):
        normalized["verified_count"] = verified_count

    unverified_count = raw.get("unverified_count")
    if isinstance(unverified_count, int):
        normalized["unverified_count"] = unverified_count

    return normalized
