"""Tests for reviewed/skipped file reconciliation (issue #1910)."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.enums.file_skip_reason import (
    FileSkipReason,
    describe_skip_reason,
)
from lintro.ai.review.file_selection import resolve_file_selection
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.skipped_file import SkippedFile


def _context(*, paths: list[str], skipped: list[SkippedFile]) -> ReviewContext:
    """Build a review context over ``paths`` with collection-stage skips.

    Args:
        paths: Repository-relative paths of the changed files.
        skipped: Collection-stage skips to attach.

    Returns:
        The assembled review context.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="head",
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=0)
            for path in paths
        ],
        unified_diff="diff",
        skipped_files=skipped,
    )


def test_selection_splits_reviewed_from_chunk_skips() -> None:
    """A file the chunker omitted is reported as skipped, not as reviewed."""
    context = _context(paths=["a.py", "b.py"], skipped=[])
    chunk_skips = [
        SkippedFile(path="b.py", reason=FileSkipReason.REPETITIVE_DIFF),
    ]

    selection = resolve_file_selection(context=context, chunk_skips=chunk_skips)

    assert_that(selection.reviewed_paths).is_equal_to(("a.py",))
    assert_that([entry.path for entry in selection.skipped]).is_equal_to(["b.py"])


def test_selection_keeps_collection_stage_skips() -> None:
    """Path-filtered files never reach ``changed_files`` but stay reported."""
    context = _context(
        paths=["a.py"],
        skipped=[SkippedFile(path="docs/x.md", reason=FileSkipReason.PATH_FILTER)],
    )

    selection = resolve_file_selection(context=context)

    assert_that(selection.reviewed_paths).is_equal_to(("a.py",))
    assert_that(selection.skipped[0].reason).is_equal_to(FileSkipReason.PATH_FILTER)


def test_selection_keeps_the_first_reason_for_a_doubly_skipped_file() -> None:
    """A file dropped twice is explained once, by the stage that dropped it."""
    context = _context(
        paths=["a.py", "b.py"],
        skipped=[SkippedFile(path="b.py", reason=FileSkipReason.PATH_FILTER)],
    )
    chunk_skips = [
        SkippedFile(path="b.py", reason=FileSkipReason.REPETITIVE_DIFF),
    ]

    selection = resolve_file_selection(context=context, chunk_skips=chunk_skips)

    assert_that(selection.skipped).is_length(1)
    assert_that(selection.skipped[0].reason).is_equal_to(FileSkipReason.PATH_FILTER)


def test_skipped_file_label_appends_detail() -> None:
    """Detail text is folded into the rendered reason when present."""
    entry = SkippedFile(
        path="pkg/item9.py",
        reason=FileSkipReason.REPETITIVE_DIFF,
        detail="same diff hunks as `pkg/item1.py`",
    )

    assert_that(entry.label).starts_with(
        describe_skip_reason(reason=FileSkipReason.REPETITIVE_DIFF),
    )
    assert_that(entry.label).contains("same diff hunks as `pkg/item1.py`")


def test_every_skip_reason_has_a_label() -> None:
    """A new reason cannot ship without prose explaining it to a reader."""
    for reason in FileSkipReason:
        assert_that(describe_skip_reason(reason=reason)).is_not_empty()
