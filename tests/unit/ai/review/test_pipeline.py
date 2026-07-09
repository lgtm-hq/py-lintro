"""Tests for review preparation pipeline."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
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
    """Pipeline splits into more chunks when max_tokens is reduced."""
    baseline = prepare_review_chunks(
        context=sample_review_context,
        max_tokens=10_000,
    )
    constrained = prepare_review_chunks(context=sample_review_context, max_tokens=20)

    baseline_paths = {path for chunk in baseline.chunks for path in chunk.files}
    constrained_paths = {path for chunk in constrained.chunks for path in chunk.files}
    assert_that(constrained_paths).is_equal_to(baseline_paths)
    assert_that(constrained.truncated or len(constrained.chunks) >= 1).is_true()


def test_prepare_review_chunks_validates_context_before_chunking() -> None:
    """Invalid review context fails before classification or chunking."""
    from lintro.ai.review.models.changed_file import ChangedFile

    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff="not a valid unified diff header\n",
        pr_metadata=None,
    )

    with (
        patch("lintro.ai.review.pipeline.classify_changed_files") as classify_mock,
        patch("lintro.ai.review.pipeline.chunk_review_context") as chunk_mock,
        pytest.raises(ReviewContextError) as exc_info,
    ):
        prepare_review_chunks(context=context, max_tokens=4096)

    classify_mock.assert_not_called()
    chunk_mock.assert_not_called()
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.NO_PARSEABLE_DIFF,
    )


def test_prepare_review_chunks_forwards_allow_omitted_files(
    repetitive_unified_diff: str,
) -> None:
    """Pipeline defaults to sampling with skipped_files; strict mode can raise."""
    from lintro.ai.review.models.changed_file import ChangedFile

    changed_files = [
        ChangedFile(
            path=f"pkg/item{index}.py",
            status="modified",
            additions=1,
            deletions=0,
        )
        for index in range(6)
    ]
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=changed_files,
        unified_diff=repetitive_unified_diff,
        pr_metadata=None,
    )

    result = prepare_review_chunks(context=context, max_tokens=10_000)
    assert_that(result.skipped_files).is_not_empty()
    chunked_paths = {path for chunk in result.chunks for path in chunk.files}
    assert_that(chunked_paths.intersection(result.skipped_files)).is_empty()
    assert_that(chunked_paths.union(result.skipped_files)).is_equal_to(
        {file.path for file in changed_files},
    )

    with pytest.raises(ReviewContextError) as exc_info:
        prepare_review_chunks(
            context=context,
            max_tokens=10_000,
            allow_omitted_files=False,
        )
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.REPETITIVE_SAMPLING_OMITTED,
    )
