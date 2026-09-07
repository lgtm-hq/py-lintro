"""State adapters between a review round and the persisted sticky state.

The reviewed-path set a matcher should trust, the inline comment ids stamped
onto the records about to be persisted, and the parser that reads a blob left
behind on an older sticky comment. Building the run record itself lives in
:mod:`lintro.ai.review.run_record_factory`, next to the model it builds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.review_state_codec import decode_state


def matcher_reviewed_paths(*, result: ReviewResult) -> frozenset[str] | None:
    """Return the reviewed-path set the matcher should use.

    An empty ``metadata.reviewed_paths`` on a resume-aware result
    (``coverage`` is set) is a true empty set — including a zero-call
    carried round — so unread findings stay open. Fixture results and
    reviews that predate the coverage field still treat the empty tuple
    as unspecified (``None``) so disappeared findings can resolve.

    Args:
        result: Current review result.

    Returns:
        Paths the provider read, or ``None`` when the field is unspecified.
    """
    if result.metadata.reviewed_paths:
        return frozenset(result.metadata.reviewed_paths)
    if result.coverage is not None:
        return frozenset()
    return None


def stamp_comment_ids(
    *,
    records: tuple[FindingRecord, ...],
    comment_ids: Mapping[str, int] | None,
) -> tuple[FindingRecord, ...]:
    """Attach captured inline comment ids to the records about to be persisted.

    Args:
        records: Records produced by this round's matching.
        comment_ids: Finding key to inline comment id, or ``None`` when no ids
            were captured.

    Returns:
        The records, each carrying its comment id when one is known. A record
        keeps the id it already had when the capture found none, so a failed
        listing never erases the anchor a later round edits.
    """
    if not comment_ids:
        return records
    return tuple(
        (
            replace(record, inline_comment_id=comment_ids[record.key])
            if record.key in comment_ids
            else record
        )
        for record in records
    )


def parse_sticky_state(*, body: str) -> ReviewState:
    """Decode the review state left behind in a sticky comment's state block.

    A missing, malformed, v1, or unknown-version blob yields an empty state
    rather than raising (#2305): a pre-v2 comment is treated as absent and the
    round starts a fresh history.

    Args:
        body: Existing sticky comment body.

    Returns:
        The decoded review state.
    """
    return decode_state(body=body)
