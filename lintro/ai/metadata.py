"""Typed helpers for AI metadata attached to tool results."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion, AISummary
    from lintro.models.core.tool_result import ToolResult


class AISummaryPayload(TypedDict):
    """Serialized summary payload for JSON output."""

    overview: str
    key_patterns: list[str]
    priority_actions: list[str]
    triage_suggestions: list[str]
    estimated_effort: str
    input_tokens: int
    output_tokens: int
    cost_estimate: float


class AIFixSuggestionPayload(TypedDict):
    """Serialized fix suggestion payload for JSON output."""

    file: str
    line: int
    code: str
    explanation: str
    confidence: str
    diff: str
    input_tokens: int
    output_tokens: int
    cost_estimate: float


class AIMetadataPayload(TypedDict, total=False):
    """Top-level AI metadata attached to ToolResult."""

    summary: AISummaryPayload
    fix_suggestions: list[AIFixSuggestionPayload]
    applied_count: int
    verified_count: int
    unverified_count: int
    fixed_count: int


def summary_to_payload(summary: AISummary) -> AISummaryPayload:
    """Convert AISummary model to JSON-serializable metadata payload."""
    return {
        "overview": summary.overview,
        "key_patterns": summary.key_patterns,
        "priority_actions": summary.priority_actions,
        "triage_suggestions": summary.triage_suggestions,
        "estimated_effort": summary.estimated_effort,
        "input_tokens": summary.input_tokens,
        "output_tokens": summary.output_tokens,
        "cost_estimate": summary.cost_estimate,
    }


def suggestion_to_payload(
    suggestion: AIFixSuggestion,
) -> AIFixSuggestionPayload:
    """Convert AIFixSuggestion model to JSON-serializable payload."""
    return {
        "file": suggestion.file,
        "line": suggestion.line,
        "code": suggestion.code,
        "explanation": suggestion.explanation,
        "confidence": suggestion.confidence,
        "diff": suggestion.diff,
        "input_tokens": suggestion.input_tokens,
        "output_tokens": suggestion.output_tokens,
        "cost_estimate": suggestion.cost_estimate,
    }


def ensure_ai_metadata(result: ToolResult) -> AIMetadataPayload:
    """Ensure a ToolResult has a mutable AI metadata container."""
    if result.ai_metadata is None:
        result.ai_metadata = {}
    return cast(AIMetadataPayload, result.ai_metadata)


def attach_summary_metadata(result: ToolResult, summary: AISummary) -> None:
    """Attach summary metadata without overwriting other AI metadata."""
    metadata = ensure_ai_metadata(result)
    metadata["summary"] = summary_to_payload(summary)


def attach_fix_suggestions_metadata(
    result: ToolResult,
    suggestions: list[AIFixSuggestion],
) -> None:
    """Attach fix suggestion metadata without overwriting summary metadata."""
    metadata = ensure_ai_metadata(result)
    existing = list(metadata.get("fix_suggestions", []))
    existing.extend(suggestion_to_payload(s) for s in suggestions)
    metadata["fix_suggestions"] = existing


def attach_fixed_count_metadata(result: ToolResult, fixed_count: int) -> None:
    """Attach per-tool AI-applied fix count for summary rendering.

    Keeps legacy ``fixed_count`` for backward compatibility.
    """
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


def normalize_ai_metadata(raw: dict[str, Any]) -> AIMetadataPayload:
    """Normalize legacy and current AI metadata into one stable shape."""
    normalized: AIMetadataPayload = {}

    summary = raw.get("summary")
    if isinstance(summary, dict):
        normalized["summary"] = cast(AISummaryPayload, summary)

    fix_suggestions = raw.get("fix_suggestions")
    if fix_suggestions is None:
        # Backward-compatible read for legacy key.
        fix_suggestions = raw.get("suggestions")
    if isinstance(fix_suggestions, list):
        normalized["fix_suggestions"] = cast(
            list[AIFixSuggestionPayload],
            [item for item in fix_suggestions if isinstance(item, dict)],
        )

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
