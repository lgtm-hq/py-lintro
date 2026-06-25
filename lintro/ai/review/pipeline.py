"""Review preparation pipeline chaining context, classification, and chunking."""

from __future__ import annotations

from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.context import _validate_review_context_diff
from lintro.ai.review.models.chunking_result import ChunkingResult
from lintro.ai.review.models.review_context import ReviewContext


def prepare_review_chunks(
    *,
    context: ReviewContext,
    max_tokens: int,
    allow_omitted_files: bool = False,
) -> ChunkingResult:
    """Classify changed files and split review context into semantic chunks.

    Args:
        context: Collected review diff context.
        max_tokens: Maximum estimated tokens per chunk diff.
        allow_omitted_files: When False, raise when repetitive-diff sampling omits
            files instead of returning them in ``skipped_files``.

    Returns:
        Chunking result with semantic groups and any truncation metadata.
    """
    _validate_review_context_diff(context=context)
    classifications = classify_changed_files(files=context.changed_files)
    return chunk_review_context(
        context=context,
        max_tokens=max_tokens,
        classifications=classifications,
        allow_omitted_files=allow_omitted_files,
    )
