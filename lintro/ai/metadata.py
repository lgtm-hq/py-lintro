"""Typed helpers for AI metadata attached to tool results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion, AISummary
    from lintro.models.core.tool_result import ToolResult


@dataclass
class AISummaryPayload:
    """Serialized summary payload for JSON output."""

    overview: str = ""
    key_patterns: list[str] = field(default_factory=list)
    priority_actions: list[str] = field(default_factory=list)
    triage_suggestions: list[str] = field(default_factory=list)
    estimated_effort: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)


@dataclass
class AIFixSuggestionPayload:
    """Serialized fix suggestion payload for JSON output."""

    file: str = ""
    line: int = 0
    code: str = ""
    tool_name: str = ""
    explanation: str = ""
    confidence: str = ""
    risk_level: str = ""
    diff: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)


@dataclass
class AIMetadataPayload:
    """Top-level AI metadata attached to ToolResult."""

    summary: AISummaryPayload | None = None
    fix_suggestions: list[AIFixSuggestionPayload] | None = None
    applied_count: int | None = None
    verified_count: int | None = None
    unverified_count: int | None = None
    fixed_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict, omitting None fields."""
        result: dict[str, Any] = {}
        if self.summary is not None:
            result["summary"] = self.summary.to_dict()
        if self.fix_suggestions is not None:
            result["fix_suggestions"] = [s.to_dict() for s in self.fix_suggestions]
        if self.applied_count is not None:
            result["applied_count"] = self.applied_count
        if self.verified_count is not None:
            result["verified_count"] = self.verified_count
        if self.unverified_count is not None:
            result["unverified_count"] = self.unverified_count
        if self.fixed_count is not None:
            result["fixed_count"] = self.fixed_count
        return result


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
        explanation=suggestion.explanation,
        confidence=suggestion.confidence,
        risk_level=suggestion.risk_level,
        diff=suggestion.diff,
        input_tokens=suggestion.input_tokens,
        output_tokens=suggestion.output_tokens,
        cost_estimate=suggestion.cost_estimate,
    )


def ensure_ai_metadata(result: ToolResult) -> dict[str, Any]:
    """Ensure a ToolResult has a mutable AI metadata container.

    Returns a raw dict for backward compatibility with existing consumers
    that access metadata by string key.
    """
    if result.ai_metadata is None:
        result.ai_metadata = {}
    return result.ai_metadata


def attach_summary_metadata(result: ToolResult, summary: AISummary) -> None:
    """Attach summary metadata without overwriting other AI metadata."""
    metadata = ensure_ai_metadata(result)
    payload = summary_to_payload(summary)
    metadata["summary"] = payload.to_dict()


def attach_fix_suggestions_metadata(
    result: ToolResult,
    suggestions: list[AIFixSuggestion],
) -> None:
    """Attach fix suggestion metadata without overwriting summary metadata."""
    metadata = ensure_ai_metadata(result)
    existing = list(metadata.get("fix_suggestions", []))
    existing.extend(suggestion_to_payload(s).to_dict() for s in suggestions)
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


def normalize_ai_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy and current AI metadata into one stable shape."""
    normalized: dict[str, Any] = {}

    summary = raw.get("summary")
    if isinstance(summary, dict):
        normalized["summary"] = summary

    fix_suggestions = raw.get("fix_suggestions")
    if fix_suggestions is None:
        # Backward-compatible read for legacy key.
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
