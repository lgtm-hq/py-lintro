"""The result a review run returns when the diff has nothing to review."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lintro.ai.model_pricing import get_context_window
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.timings import ReviewPhase, ReviewTimingRecorder

if TYPE_CHECKING:
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.session import ReviewSessionOptions

__all__ = ["empty_review_result"]


def empty_review_result(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
) -> ReviewResult:
    """Return an empty result when no changes are present.

    Args:
        context: Collected review diff context.
        options: Session options for the run.

    Returns:
        A result recording that the review had nothing to look at.
    """
    provider = options.provider
    depth = options.depth
    checklist_items = options.checklist_items
    context_window_override = options.context_window_override
    context_collection_seconds = options.context_collection_seconds
    empty_timings = ReviewTimingRecorder()
    empty_timings.add_phase(
        name=ReviewPhase.CONTEXT_COLLECTION,
        seconds=context_collection_seconds,
    )
    context_window = get_context_window(
        model=provider.model_name,
        override=context_window_override,
    )
    metadata = ReviewMetadata(
        model=provider.model_name,
        provider=provider.name,
        context_window=context_window,
        depth=depth,
        chunks_total=0,
        chunks_current=0,
        files_reviewed=0,
        files_total=0,
        checklist_items=len(checklist_items),
        token_usage={"prompt": 0, "completion": 0, "total": 0},
        cost_estimate_usd=0.0,
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        timestamp=datetime.now(tz=UTC).isoformat(),
        lint_facts_note=options.lint_note,
        phase_timings={
            "context_collection": max(context_collection_seconds, 0.0),
            "provider": 0.0,
            "parse_merge": 0.0,
        },
        # Nothing ran after context collection, so the run's duration and
        # the timings total are the same figure (#2148).
        duration_seconds=max(context_collection_seconds, 0.0),
        timings=empty_timings.build(
            total_seconds=max(context_collection_seconds, 0.0),
            max_parallel=1,
        ),
    )
    return ReviewResult(
        metadata=metadata,
        summary="No changes found to review.",
        findings=(),
        coverage=CoverageCounts(),
    )
