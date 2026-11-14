"""Hold findings a moved base pushed out of the pull request's diff (#3467).

The diff gate (:mod:`lintro.ai.review.diff_gate`) drops every finding whose
line lies outside the pull request's hunks. While the base stays put, a
recorded finding sits on a line the pull request wrote, so the provider can
report it again and its absence means the line was fixed. A rebase, or a
checkpoint resumed against a newer base, can move that line into the base
itself: the code is unchanged and still wrong, but the line is no longer part
of the diff. Any re-report there is dropped by the gate, and the matcher used
to read the silence as a fix and resolve the thread.

So when the merge-base the record was last placed under differs from this
round's, a prior open finding whose line sits outside every hunk of its file
at this head (or whose file has no line hunks at all) is held at its stored
location (:data:`~lintro.ai.review.models.delta_plan.LOCATION_BASE_MOVED`):
carried unverified, never resolved. A line git cannot place at this head is
left to the matcher. That is how a fix that rewrites or deletes the reported
line looks, and a line the pull request edited lies in its diff, where a
re-report survives the gate. A merge-base that cannot be determined counts as
moved, since holding a live thread is safe and resolving one is not.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.ai.review.delta import merge_base
from lintro.ai.review.delta_lines import map_open_findings, pr_renames
from lintro.ai.review.diff_gate import hunks_from_diff
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.models.delta_plan import LOCATION_BASE_MOVED, LOCATION_HOLDS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lintro.ai.review.diff_gate import FileHunks
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_state import ReviewState

__all__ = ["hold_across_base_moves"]

Relocation = tuple[str, str, int, str]


def hold_across_base_moves(
    *,
    prior: ReviewState | None,
    context: ReviewContext,
    repo_root: str,
    relocations: tuple[Relocation, ...],
    at_head: bool,
) -> tuple[Relocation, ...]:
    """Hold every open finding a moved base pushed out of the diff.

    Args:
        prior: State of the previous rounds, or ``None``.
        context: This round's context: base, head and the pull request's
            unified diff.
        repo_root: The repository holding the commits.
        relocations: The round's relocations, ``(key, path, line, line_sha)``.
        at_head: Whether ``relocations`` place each record at this head (a
            full round). A delta round's are at its base, so the head
            placement of the records whose merge-base moved is computed here.

    Returns:
        ``relocations`` with a :data:`LOCATION_BASE_MOVED` hold replacing the
        entry of every record that has to be held.
    """
    if prior is None or not context.head_ref:
        return relocations
    current = merge_base(
        repo_root=repo_root,
        base_sha=context.base_ref,
        head_sha=context.head_ref,
    )
    moved = [
        record
        for record in prior.findings
        if record.status is FindingStatus.OPEN
        and record.file
        and _base_moved_since(
            record=record,
            prior=prior,
            repo_root=repo_root,
            current=current,
        )
    ]
    if not moved:
        return relocations
    head_placement = (
        relocations
        if at_head
        else map_open_findings(
            prior=replace(prior, findings=tuple(moved)),
            since_sha=context.head_ref,
            head_sha=context.head_ref,
            repo_root=repo_root,
            renamed_from=pr_renames(changed_files=context.changed_files),
        ).relocations
    )
    placed = {entry[0]: entry for entry in head_placement}
    hunks = hunks_from_diff(diff=context.unified_diff)
    holds = {
        record.key: (record.key, record.file, record.line, LOCATION_BASE_MOVED)
        for record in moved
        if _outside_diff(
            record=record,
            placed=placed.get(record.key),
            head_sha=context.head_ref,
            hunks=hunks,
        )
    }
    if not holds:
        return relocations
    kept = tuple(entry for entry in relocations if entry[0] not in holds)
    return kept + tuple(holds.values())


def _base_moved_since(
    *,
    record: FindingRecord,
    prior: ReviewState,
    repo_root: str,
    current: str,
) -> bool:
    """Tell whether the merge-base moved since the record's head.

    Args:
        record: An open prior record.
        prior: State of the previous rounds.
        repo_root: The repository holding the commits.
        current: ``merge-base(base, head)`` for this round, or ``""``.

    Returns:
        True when the record's merge-base differs from ``current`` or either
        one is unknown.
    """
    recorded = next(
        (
            run.identity.merge_base
            for run in reversed(prior.runs)
            if run.identity.sha == record.line_sha and run.identity.merge_base
        ),
        "",
    )
    if not recorded and prior.base_sha and record.line_sha:
        # A checkpoint carries no finished run; its base names the round's.
        recorded = merge_base(
            repo_root=repo_root,
            base_sha=prior.base_sha,
            head_sha=record.line_sha,
        )
    return not recorded or not current or recorded != current


def _outside_diff(
    *,
    record: FindingRecord,
    placed: Relocation | None,
    head_sha: str,
    hunks: Mapping[str, FileHunks],
) -> bool:
    """Tell whether a re-report of the record would be dropped at this head.

    Args:
        record: An open prior record whose merge-base moved.
        placed: Its relocation at this head, or ``None`` when its stored
            location needed none.
        head_sha: This round's head.
        hunks: The pull request's hunks at this head, per file.

    Returns:
        True when its file has no line hunks, or its line was placed and lies
        outside every hunk. False for a record already held and for a line
        git could not place.
    """
    if placed is None:
        path = record.file
        line = record.line if record.line_sha == head_sha else None
    elif placed[3] in LOCATION_HOLDS:
        return False
    else:
        path = placed[1]
        line = placed[2] if placed[3] == head_sha else None
    file_hunks = hunks.get(path)
    if file_hunks is None or not file_hunks.hunks:
        return True
    return line is not None and line > 0 and not file_hunks.contains(line)
