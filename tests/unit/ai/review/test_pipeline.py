"""Tests for review preparation pipeline."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.group_labels import REL_SOURCE_TEST, REL_WORKFLOW_SCRIPT_TEST
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.pipeline import prepare_review_chunks


def test_prepare_review_chunks_runs_classification_and_chunking(
    sample_review_context: ReviewContext,
) -> None:
    """Pipeline chains classification and semantic chunking."""
    result = prepare_review_chunks(
        context=sample_review_context,
        max_tokens=10_000,
    )

    assert_that(result.chunks).is_not_empty()
    chunked_paths = {path for chunk in result.chunks for path in chunk.files}
    assert_that(chunked_paths).is_equal_to(
        {file.path for file in sample_review_context.changed_files},
    )
    relationships = {chunk.relationship for chunk in result.chunks}
    assert_that(relationships).contains(REL_WORKFLOW_SCRIPT_TEST, REL_SOURCE_TEST)


def test_prepare_review_chunks_honors_explicit_token_budget(
    sample_review_context: ReviewContext,
) -> None:
    """Pipeline chunking succeeds for an explicit max_tokens budget."""
    result = prepare_review_chunks(context=sample_review_context, max_tokens=4096)

    chunked_paths = {path for chunk in result.chunks for path in chunk.files}
    assert_that(chunked_paths).is_equal_to(
        {file.path for file in sample_review_context.changed_files},
    )
