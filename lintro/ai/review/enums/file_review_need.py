"""Why a changed file is queued for review in a resume round (#2154)."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["FILE_REVIEW_NEED_PRIORITY", "FileReviewNeed"]


class FileReviewNeed(StrEnum):
    """Classification of a review-eligible file for this round.

    Queue order under a cap is never-reviewed → directly changed →
    model-flagged → group/import-invalidated. Covered files are skipped
    (zero provider calls) unless ``--full`` discards carried coverage.

    Attributes:
        NEVER_REVIEWED: No coverage entry for this path.
        DIRECTLY_CHANGED: Stored hash differs from the current patch hash.
        MODEL_FLAGGED: The reviewer asked for a re-read via ``flagged_files``.
        OPEN_THREAD: A finding from a prior round is still open on this
            file (#2627); a delta round re-reads it so the thread can be
            re-litigated against its own code.
        GROUP_INVALIDATED: A semantic-group mate changed since this review.
        IMPORT_INVALIDATED: A one-hop Python import dependency changed.
        COVERED: Current hash matches a stored entry and nothing invalidated it.
    """

    NEVER_REVIEWED = auto()
    DIRECTLY_CHANGED = auto()
    MODEL_FLAGGED = auto()
    OPEN_THREAD = auto()
    GROUP_INVALIDATED = auto()
    IMPORT_INVALIDATED = auto()
    COVERED = auto()


#: Numeric queue rank; lower runs first under a cost cap. Group and import
#: share a rank so they interleave by path after the higher-priority buckets.
FILE_REVIEW_NEED_PRIORITY: dict[FileReviewNeed, int] = {
    FileReviewNeed.NEVER_REVIEWED: 0,
    FileReviewNeed.DIRECTLY_CHANGED: 1,
    FileReviewNeed.MODEL_FLAGGED: 2,
    FileReviewNeed.OPEN_THREAD: 2,
    FileReviewNeed.GROUP_INVALIDATED: 3,
    FileReviewNeed.IMPORT_INVALIDATED: 3,
    FileReviewNeed.COVERED: 99,
}
