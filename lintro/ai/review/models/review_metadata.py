"""Review run metadata."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ReviewMetadata:
    """Metadata describing an AI review run.

    Attributes:
        model: Model identifier used for the review.
        provider: Provider name (anthropic, openai, etc.).
        context_window: Model context window in tokens.
        depth: Review depth level (1-3).
        chunks_total: Total semantic chunks processed.
        chunks_current: Chunks included in this result view.
        files_reviewed: Number of changed files included in review.
        files_total: Total changed files in the diff.
        checklist_items: Number of checklist items in the prompt.
        token_usage: Aggregated token usage counters.
        cost_estimate_usd: Estimated cost in USD.
        base_ref: Base git ref for the diff.
        head_ref: Head git ref for the diff.
        timestamp: ISO 8601 UTC timestamp of the review run.
    """

    model: str
    provider: str
    context_window: int
    depth: int
    chunks_total: int
    chunks_current: int
    files_reviewed: int
    files_total: int
    checklist_items: int
    token_usage: dict[str, int] = field(default_factory=dict)
    cost_estimate_usd: float = 0.0
    base_ref: str = ""
    head_ref: str = ""
    timestamp: str = ""
