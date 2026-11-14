"""Delta-round helpers for the finding matcher (#2627).

Two decisions :func:`lintro.ai.review.finding_matcher.match_findings` makes
on a delta round live here so the matcher stays within its size: which
prior open finding is carried because the round read its file but not its
line, and which record key each current finding was assigned so the JSON
surface can carry it as ``finding_id``. The merge-base stamp the persisting
callers put on each reported record (#3467) lives here too.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.finding_identity import fingerprint_for
from lintro.ai.review.models.delta_plan import (
    LOCATION_BASE_MOVED,
    LOCATION_HOLDS,
    LOCATION_UNCHECKED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lintro.ai.review.models.finding_match_result import FindingMatchResult
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "_carry_absent",
    "_finding_positions",
    "_held_location",
    "_line_unread",
    "_relocate",
    "stamp_sightings",
]

#: Holds that keep a record open even when its path left the diff: one beyond
#: the mapping cap (#3413), and one a moved base pushed out of the diff (#3467).
_NEVER_RESOLVED = frozenset({LOCATION_UNCHECKED, LOCATION_BASE_MOVED})


def _finding_positions(
    *,
    findings: Sequence[ReviewFinding],
    inline_records: Sequence[FindingRecord],
    note_records: Sequence[FindingRecord],
) -> tuple[dict[str, list[int]], dict[int, str]]:
    """Map current records back to the findings they were built from (#2627).

    Args:
        findings: The round's findings in reported order.
        inline_records: Records for the ``posted_inline`` findings, in order.
        note_records: Records for the note-tier findings, in order.

    Returns:
        ``(positions_by_fingerprint, finding_ids)``: for each fingerprint the
        finding index of each inline record in group order, and the record
        key already final for every note (notes are round-local).
    """
    inline_positions = [i for i, f in enumerate(findings) if f.posted_inline]
    note_positions = [i for i, f in enumerate(findings) if not f.posted_inline]
    positions_by_fingerprint: dict[str, list[int]] = defaultdict(list)
    for position, record in zip(inline_positions, inline_records, strict=True):
        positions_by_fingerprint[record.fingerprint].append(position)
    finding_ids = {
        position: record.key
        for position, record in zip(note_positions, note_records, strict=True)
    }
    return positions_by_fingerprint, finding_ids


def _relocate(
    *,
    record: FindingRecord,
    relocations: Mapping[str, tuple[str, int, str]] | None,
) -> FindingRecord:
    """Move a prior open record to where the delta round placed it (#3403).

    Args:
        record: A prior record.
        relocations: ``{key: (path, line, line_sha)}`` from the round.

    Returns:
        The record at its mapped location and coordinate head; resolved
        records, records the round did not map and records it held in place
        (#3413) are returned unchanged.
    """
    moved = relocations.get(record.key) if relocations else None
    if (
        moved is None
        or record.status is not FindingStatus.OPEN
        or moved[2] in LOCATION_HOLDS
    ):
        return record
    path, line, line_sha = moved
    if path == record.file:
        return replace(record, line=line, line_sha=line_sha)
    # A rename moves the record, not its stable key: it pairs under its new
    # path's identity while keeping the key its thread marker carries.
    moved_fingerprint = fingerprint_for(
        file=path,
        category=record.category,
        title=record.title,
    )
    return replace(
        record,
        file=path,
        line=line,
        line_sha=line_sha,
        match_fingerprint=(
            "" if moved_fingerprint == record.fingerprint else moved_fingerprint
        ),
        origin_path=record.origin_path_at(path=path),
    )


def _held_location(
    *,
    key: str,
    relocations: Mapping[str, tuple[str, int, str]] | None,
) -> str:
    """Return the hold the round placed on a record's location, or ``""``.

    Args:
        key: The record's key.
        relocations: ``{key: (path, line, line_sha)}`` from the round.

    Returns:
        :data:`~lintro.ai.review.models.delta_plan.LOCATION_UNMAPPED`,
        :data:`~lintro.ai.review.models.delta_plan.LOCATION_UNCHECKED`,
        :data:`~lintro.ai.review.models.delta_plan.LOCATION_BASE_MOVED`, or
        ``""`` when the round placed the record or did not touch it.
    """
    moved = relocations.get(key) if relocations else None
    return moved[2] if moved is not None and moved[2] in LOCATION_HOLDS else ""


def _carry_absent(
    *,
    record: FindingRecord,
    held: bool,
    location_hold: str,
    reviewed_paths: frozenset[str] | None,
    departed_paths: frozenset[str] | None,
    reviewed_ranges: Mapping[str, tuple[tuple[int, int], ...]] | None,
    range_carries: set[str],
    unverified_carries: set[str],
) -> bool:
    """Decide whether a prior open finding absent this round is carried.

    Args:
        record: The prior open record nothing matched.
        held: Whether a note or a merged duplicate holds it open.
        location_hold: :func:`_held_location` for the record. A record git
            could not place is not fixed by its path leaving the diff, and one
            beyond the mapping cap (#3413) or pushed out of the diff by a
            moved base (#3467) is never resolved.
        reviewed_paths: Files re-reviewed this round, or ``None``.
        departed_paths: Paths that left the diff, or ``None``.
        reviewed_ranges: Line ranges a delta round read per narrowed file.
        range_carries: Receives the record's key when the only reason to
            carry is a delta round leaving its line unread (#2627).
        unverified_carries: Receives the record's key when it is carried
            only because the round did not read it, so nothing re-judged it
            against this head (#3403). The matcher stores that on the record
            as ``unverified``; it never changes the record's severity.

    Returns:
        True to carry it rather than resolve it.
    """
    path = record.file
    departed = departed_paths is not None and path in departed_paths
    stranded = location_hold in _NEVER_RESOLVED or (departed and bool(location_hold))
    left_diff = departed and not stranded
    unread = (reviewed_paths is not None and path not in reviewed_paths) or stranded
    line_unread = _line_unread(
        path=path,
        line=record.line,
        reviewed_ranges=reviewed_ranges,
    )
    carry = ((unread or line_unread) and not left_diff) or held
    if carry and line_unread and not unread and not held:
        range_carries.add(record.key)
    if carry and not held:
        unverified_carries.add(record.key)
    return carry


def _line_unread(
    *,
    path: str,
    line: int,
    reviewed_ranges: Mapping[str, tuple[tuple[int, int], ...]] | None,
) -> bool:
    """Tell whether a delta round left this line unread on a narrowed file.

    Args:
        path: The finding's file.
        line: The finding's line.
        reviewed_ranges: The ranges read per narrowed file, or ``None`` on a
            full round.

    Returns:
        True when the file was narrowed and no read range holds the line.
    """
    if reviewed_ranges is None:
        return False
    ranges = reviewed_ranges.get(path)
    if ranges is None:
        return False
    # A non-positive range is the "nothing shown" sentinel: it holds no line,
    # findings at line 0 included.
    return not any(start <= line <= end for start, end in ranges if start > 0)


def stamp_sightings(
    *,
    match: FindingMatchResult,
    merge_base: str,
) -> FindingMatchResult:
    """Record the merge-base each finding reported this round was seen under.

    A record the round reported passed the diff gate under ``merge_base``,
    so a later round can tell whether a moved base may have pushed it out of
    the diff (``FindingRecord.placement_merge_base``, #3467). Records carried,
    relocated or held without a report keep the merge-base they had.

    Args:
        match: The round's match.
        merge_base: ``merge-base(base, head)`` for the round, or ``""`` when
            unknown.

    Returns:
        The match with its reported records stamped.
    """
    sighted = frozenset(match.finding_ids)
    return replace(
        match,
        records=tuple(
            (
                replace(record, placement_merge_base=merge_base)
                if record.key in sighted and record.status is FindingStatus.OPEN
                else record
            )
            for record in match.records
        ),
    )
