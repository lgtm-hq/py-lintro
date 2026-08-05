"""Reconciliation of which changed files a review round actually looked at.

Two independent stages can drop a changed file: context collection (a
``--path`` filter) and chunking (repetitive-diff sampling). Each records its
own :class:`~lintro.ai.review.models.skipped_file.SkippedFile` entries; this
module merges them into the single reviewed/skipped split that run metadata
and the per-review comment body report (#1910).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.skipped_file import SkippedFile

__all__ = ["FileSelection", "resolve_file_selection"]


@dataclass(frozen=True, slots=True)
class FileSelection:
    """The reviewed/skipped split for one review round.

    Attributes:
        reviewed_paths: Repository-relative paths the review looked at, sorted.
        skipped: Excluded files with their reasons, sorted by path.
    """

    reviewed_paths: tuple[str, ...]
    skipped: tuple[SkippedFile, ...]


def resolve_file_selection(
    *,
    context: ReviewContext,
    chunk_skips: Sequence[SkippedFile] = (),
) -> FileSelection:
    """Split a round's changed files into reviewed and skipped sets.

    A path skipped by more than one stage keeps the earliest reason recorded
    for it, so a file dropped by the ``--path`` filter is never re-explained
    as a sampling omission.

    Args:
        context: Collected review context, carrying any collection-stage skips.
        chunk_skips: Per-file skips recorded by the chunker.

    Returns:
        The reviewed paths and the skipped files with their reasons.
    """
    skipped_by_path: dict[str, SkippedFile] = {}
    for entry in (*context.skipped_files, *chunk_skips):
        skipped_by_path.setdefault(entry.path, entry)

    reviewed = sorted(
        {
            changed_file.path
            for changed_file in context.changed_files
            if changed_file.path not in skipped_by_path
        },
    )
    skipped = tuple(skipped_by_path[path] for path in sorted(skipped_by_path))
    return FileSelection(reviewed_paths=tuple(reviewed), skipped=skipped)
