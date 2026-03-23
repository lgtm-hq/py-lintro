"""Typed helpers for AI metadata attached to tool results."""

from lintro.ai.metadata.fix_suggestion_payload import AIFixSuggestionPayload
from lintro.ai.metadata.helpers import (
    attach_fix_suggestions_metadata,
    attach_fixed_count_metadata,
    attach_summary_metadata,
    attach_telemetry_metadata,
    attach_validation_counts_metadata,
    ensure_ai_metadata,
    normalize_ai_metadata,
    suggestion_to_payload,
    summary_to_payload,
)
from lintro.ai.metadata.summary_payload import AISummaryPayload

__all__ = [
    "AIFixSuggestionPayload",
    "AISummaryPayload",
    "attach_fix_suggestions_metadata",
    "attach_fixed_count_metadata",
    "attach_summary_metadata",
    "attach_telemetry_metadata",
    "attach_validation_counts_metadata",
    "ensure_ai_metadata",
    "normalize_ai_metadata",
    "suggestion_to_payload",
    "summary_to_payload",
]
