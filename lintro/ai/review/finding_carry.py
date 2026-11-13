"""Delta-round helpers for the finding matcher (#2627).

Two decisions :func:`lintro.ai.review.finding_matcher.match_findings` makes
on a delta round live here so the matcher stays within its size: which
prior open finding is carried because the round read its file but not its
line, and which record key each current finding was assigned so the JSON
surface can carry it as ``finding_id``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.finding_identity import fingerprint_for

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = ["_carry_absent", "_finding_positions", "_line_unread", "_relocate"]


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
        records and records the round did not map are returned unchanged.
    """
    moved = relocations.get(record.key) if relocations else None
    if moved is None or record.status is not FindingStatus.OPEN:
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
    )


def _carry_absent(
    *,
    record: FindingRecord,
    held: bool,
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
    left_diff = departed_paths is not None and path in departed_paths
    unread = reviewed_paths is not None and path not in reviewed_paths
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
