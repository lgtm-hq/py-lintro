"""Hold findings a moved base pushed out of the pull request's diff (#3467).

The diff gate (:mod:`lintro.ai.review.diff_gate`) drops every finding whose
line lies outside the pull request's hunks. While the base stays put, a
recorded finding sits on a line the pull request wrote, so the provider can
report it again and its absence means the line was fixed. A rebase, or a
checkpoint resumed against a newer base, can move that line into the base
itself: the code is unchanged and still wrong, but the line is no longer part
of the diff. Any re-report there is dropped by the gate, and the matcher used
to read the silence as a fix and resolve the thread.

Each record carries the merge-base it was last reported under
(``FindingRecord.placement_merge_base``), kept through carries and holds, so
the evidence survives persistence, a reload and the run-history cap. When it
differs from this round's merge-base, or either is unknown (a record written
before the field existed, a shallow checkout git cannot answer in), the
record is held at its stored location
(:data:`~lintro.ai.review.models.delta_plan.LOCATION_BASE_MOVED`), carried
unverified and never resolved, unless this round can still re-judge it:

* the pull request's diff has no section for its file at this head (a pure
  rename), so the gate keeps any report there;
* its line, placed at this head, lies inside one of the pull request's hunks
  of its file, where a re-report survives the gate (a binary or mode-only
  section has no hunks, so it never qualifies); or
* git cannot place the line because it was rewritten or deleted since the
  record's head, and one of the pull request's own hunks covers the line's
  position in that rewrite at this head. That is a fix the pull request
  made. A rewrite the base made is not, and is held, even when a wider base
  rewrite merely reaches into a pull-request hunk further down.

A record already held for another reason keeps that hold.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.ai.review.delta_lines import map_open_findings, pr_renames
from lintro.ai.review.diff_gate import hunks_from_diff
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.git_line_map import file_hunks
from lintro.ai.review.models.delta_plan import LOCATION_BASE_MOVED, LOCATION_HOLDS

if TYPE_CHECKING:
    from lintro.ai.review.git_line_map import Hunks
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_state import ReviewState

__all__ = ["hold_across_base_moves"]

Relocation = tuple[str, str, int, str]
Span = tuple[int, int]


def hold_across_base_moves(
    *,
    prior: ReviewState | None,
    context: ReviewContext,
    repo_root: str,
    relocations: tuple[Relocation, ...],
    at_head: bool,
    merge_base: str,
) -> tuple[Relocation, ...]:
    """Hold every open finding a moved base pushed out of the diff.

    Args:
        prior: State of the previous rounds, or ``None``.
        context: This round's context: head, changed files and the pull
            request's unified diff.
        repo_root: The repository holding the commits.
        relocations: The round's relocations, ``(key, path, line, line_sha)``.
        at_head: Whether ``relocations`` place each record at this head (a
            full round). A delta round's are at its base, so the head
            placement of the records whose merge-base moved is computed here.
        merge_base: ``merge-base(base, head)`` for this round, or ``""`` when
            git could not say.

    Returns:
        ``relocations`` with a :data:`LOCATION_BASE_MOVED` hold replacing the
        entry of every record that has to be held.
    """
    head_sha = context.head_ref
    if prior is None or not head_sha:
        return relocations
    moved = [
        record
        for record in prior.findings
        if record.status is FindingStatus.OPEN and record.file
        # The gate keeps a finding with no line wherever it is reported.
        and record.line > 0
        # An unknown merge-base (a shallow checkout) proves nothing, even
        # when the record's is unknown too.
        and (not merge_base or record.placement_merge_base != merge_base)
    ]
    if not moved:
        return relocations
    head_placement = (
        relocations
        if at_head
        else map_open_findings(
            prior=replace(prior, findings=tuple(moved)),
            since_sha=head_sha,
            head_sha=head_sha,
            repo_root=repo_root,
            renamed_from=pr_renames(changed_files=context.changed_files),
        ).relocations
    )
    placed = {entry[0]: entry for entry in head_placement}
    pr_spans = {
        path: hunks.ranges
        for path, hunks in hunks_from_diff(diff=context.unified_diff).items()
    }
    rewrites: dict[tuple[str, str, str], Hunks] = {}
    holds: dict[str, Relocation] = {}
    for record in moved:
        placement = placed.get(record.key)
        if placement is not None and placement[3] in LOCATION_HOLDS:
            continue
        path = record.file if placement is None else placement[1]
        if placement is None:
            line = record.line if record.line_sha == head_sha else None
        else:
            line = placement[2] if placement[3] == head_sha else None
        spans = pr_spans.get(path)
        if spans is None:
            # The gate keeps a report on a file the diff has no section for
            # (a pure rename), so the round can still re-judge the finding.
            continue
        if line is not None:
            region: Span | None = (line, line)
        else:
            key = (record.line_sha, record.file, path)
            if key not in rewrites:
                rewrites[key] = (
                    file_hunks(
                        repo_root=repo_root,
                        from_sha=record.line_sha,
                        to_sha=head_sha,
                        old_path=record.file,
                        new_path=path,
                    )
                    if record.line_sha
                    else None
                )
            region = _region_at_head(line=record.line, hunks=rewrites[key])
        if region is None or not _overlaps(region=region, spans=spans):
            holds[record.key] = _hold(record=record)
    if not holds:
        return relocations
    kept = tuple(entry for entry in relocations if entry[0] not in holds)
    return kept + tuple(holds.values())


def _hold(*, record: FindingRecord) -> Relocation:
    """Return the relocation that holds a record where it was stored.

    Args:
        record: The record to hold.

    Returns:
        Its :data:`LOCATION_BASE_MOVED` relocation.
    """
    return (record.key, record.file, record.line, LOCATION_BASE_MOVED)


def _region_at_head(*, line: int, hunks: Hunks) -> Span | None:
    """Return where a recorded line ended up at this head.

    Args:
        line: The line at the record's head.
        hunks: ``(old_start, old_len, new_len)`` from the record's head to
            this head, or ``None`` when git could not diff the range.

    Returns:
        The line itself when no hunk touched it, the line's own position in
        the hunk that rewrote it (its offset from the hunk's old start,
        capped at the replacement's last line), the two lines around a
        deletion, or ``None`` when the hunks are unknown.
    """
    if hunks is None:
        return None
    shift = 0
    for old_start, old_len, new_len in hunks:
        if old_len == 0:
            if old_start < line:
                shift += new_len
            continue
        old_end = old_start + old_len - 1
        if old_start <= line <= old_end:
            start = old_start + shift
            if new_len == 0:
                return max(start - 1, 1), start
            # The line's own position in the replacement, not the whole span:
            # a wide rewrite the pull request only grazes is not its fix.
            position = start + min(line - old_start, new_len - 1)
            return position, position
        if old_end < line:
            shift += new_len - old_len
    return line + shift, line + shift


def _overlaps(*, region: Span, spans: tuple[Span, ...]) -> bool:
    """Tell whether a region meets one of the pull request's hunks.

    Args:
        region: An inclusive ``(start, end)`` line span at this head.
        spans: The pull request's hunk spans of the file at this head.

    Returns:
        True when any span shares a line with the region.
    """
    start, end = region
    return any(low <= end and start <= high for low, high in spans)
