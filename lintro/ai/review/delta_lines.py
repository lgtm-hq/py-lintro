"""Where each open finding sits at a round's base (#3403, #3413).

A delta round (#2627) narrows a changed file to its ``since..head`` hunks. A
prior open finding whose line falls outside every hunk is then carried
unread: the matcher cannot resolve what the round did not show. When the fix
landed elsewhere in the file (tests appended below a test-gap finding, a
merge block widened below the line a logic bug was reported on), that
carry repeats every round and the finding never closes. These helpers name
the lines a delta has to show, so :func:`lintro.ai.review.delta.apply_delta_hunks`
can read such a file whole instead.

A record's location is in the coordinates of the head that last reported it
(:attr:`~lintro.ai.review.models.finding_record.FindingRecord.line_sha`). A
round that carried it unread did not move it, so before the delta check the
location is mapped forward to the delta's base through ``line_sha..since``,
following a rename git reports. A full round maps every record to its own
head the same way (#3413). The mapped location is also what the matcher
judges the finding at (:attr:`MappedFindings.relocations`). A rename git
paired below :data:`~lintro.ai.review.git_line_map.WEAK_RENAME_SIMILARITY`
never moves a record: it is held where it was stored
(:data:`~lintro.ai.review.models.delta_plan.LOCATION_WEAK_RENAME`, #3466),
because similarity alone can pair the record's file with an unrelated
look-alike while the finding lives on in another file. When a location
cannot be mapped (no recorded head, git cannot diff the range, the line
itself was edited, or a rename is ambiguous) the file is read whole: failing
open here is exactly the false "fixed" #3403 is about.

When git reads a rename as a delete plus an add, or cannot reach a record's
head at all (a rewritten branch in a shallow checkout), the pull request's
own rename metadata moves the record to its file's new path. That metadata
spans the whole pull request and names only a file's path at the base, so it
moves a record only while the record still sits there. A record an earlier
round carried across a rename is not looked up under the path it was first
tracked at (``FindingRecord.origin_path``): path metadata cannot prove which
file a delete plus an add continues, and guessing resolved live threads
(#3432). When no rename names the record's own path, the record is
held where it is (:data:`~lintro.ai.review.models.delta_plan.LOCATION_UNMAPPED`)
and its path leaving the diff never counts as a fix. A round maps at most
:data:`MAX_MAPPED_FINDINGS` records through git; any beyond that are held
(:data:`~lintro.ai.review.models.delta_plan.LOCATION_UNCHECKED`) and never
resolved that round. The git primitives live in
:mod:`lintro.ai.review.git_line_map`, the rename lookups in
:mod:`lintro.ai.review.rename_lineage`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.git_line_map import (
    PINNED_DIFF_FLAGS,
    file_hunks,
    literal_pathspec,
    map_line,
)
from lintro.ai.review.models.delta_plan import (
    LOCATION_UNCHECKED,
    LOCATION_UNMAPPED,
    LOCATION_WEAK_RENAME,
)
from lintro.ai.review.rename_lineage import (
    RoundLookups,
    cached_renames,
    unpaired_rename,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from lintro.ai.review.models.changed_file import ChangedFile
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "MAX_MAPPED_FINDINGS",
    "PINNED_DIFF_FLAGS",
    "MappedFindings",
    "literal_pathspec",
    "map_open_findings",
    "pr_renames",
    "shows_every_line",
]

#: Records one round maps through git (one ``git diff -U0`` per distinct
#: ``(line_sha, path)``); the rest are held open and unresolved (#3413).
MAX_MAPPED_FINDINGS = 200


@dataclass(frozen=True, slots=True)
class MappedFindings:
    """The open findings' locations at a round's base.

    Attributes:
        lines: Lines per file a narrowed file must show, in the base's
            coordinates; ``None`` for a file that must be read whole because
            a finding on it could not be placed there.
        relocations: ``(key, path, line, line_sha)`` for each open record
            whose stored location was not already in the base's coordinates.
            ``line_sha`` is the base when the line was mapped, and empty when
            only the path is known (a rename whose line could not be placed),
            which makes the next round read the file whole again. It is
            :data:`LOCATION_UNMAPPED` or :data:`LOCATION_UNCHECKED` for a
            record held at its stored location (#3413).
    """

    lines: dict[str, tuple[int, ...] | None] = field(default_factory=dict)
    relocations: tuple[tuple[str, str, int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _Placement:
    """Where one open record sits at a round's base.

    Attributes:
        record: The open record.
        read_path: The file the round reads for it; ``""`` for a record held
            beyond the mapping cap, which asks for no lines.
        line: Its line in ``read_path``, or ``None`` when that file must be
            read whole.
        relocation: ``(key, path, line, line_sha)`` when its stored location
            changes or is held, else ``None``.
    """

    record: FindingRecord
    read_path: str
    line: int | None
    relocation: tuple[str, str, int, str] | None


def pr_renames(*, changed_files: Iterable[ChangedFile]) -> dict[str, str]:
    """Return the pull request's renames from its changed-file metadata.

    Args:
        changed_files: The round's changed files (git or the files API).

    Returns:
        Source path to new path for every renamed file; copies leave their
        source in place and are not listed.
    """
    return {
        item.previous_path: item.path
        for item in changed_files
        if item.status is ChangedFileStatus.RENAMED and item.previous_path
    }


def map_open_findings(
    *,
    prior: ReviewState | None,
    since_sha: str,
    head_sha: str,
    repo_root: str,
    renamed_from: Mapping[str, str] | None = None,
) -> MappedFindings:
    """Place every open prior finding at ``since_sha`` (#3403).

    Every open record counts, posted inline or not: a finding shown only in
    the sticky has no thread to re-litigate it, so re-reading its line is the
    only way a later round can see that it was fixed.

    Args:
        prior: State of the previous rounds, or ``None``.
        since_sha: The delta's base, the prior round's head; a full round
            passes its own head (#3413).
        head_sha: This round's head; a file renamed since the base is keyed
            by its head path, whose chunk the round reads.
        repo_root: The repository holding the commits.
        renamed_from: The pull request's renames (:func:`pr_renames`), used
            when git cannot list the renames since a record's head.

    Returns:
        The mapped lines and the relocations to judge findings at.
    """
    if prior is None:
        return MappedFindings()
    lookups = RoundLookups(repo_root=repo_root, renamed_from=renamed_from or {})
    placements: list[_Placement] = []
    mapped = 0
    for record in prior.findings:
        if record.status is not FindingStatus.OPEN or not record.file:
            continue
        if record.line_sha and record.line_sha != since_sha:
            mapped += 1
            if mapped > MAX_MAPPED_FINDINGS:
                placements.append(
                    _Placement(
                        record=record,
                        read_path="",
                        line=None,
                        relocation=(
                            record.key,
                            record.file,
                            record.line,
                            LOCATION_UNCHECKED,
                        ),
                    ),
                )
                continue
        placements.append(
            _map_record(
                record=record,
                since_sha=since_sha,
                head_sha=head_sha,
                lookups=lookups,
            ),
        )
    return _collect(placements=placements)


def _map_record(
    *,
    record: FindingRecord,
    since_sha: str,
    head_sha: str,
    lookups: RoundLookups,
) -> _Placement:
    """Place one open record at ``since_sha`` and name the file to read.

    Args:
        record: An open record within the mapping cap.
        since_sha: The head to place it at.
        head_sha: This round's head.
        lookups: The round's git answers.

    Returns:
        The record's placement.
    """
    path, line, hold = _place(record=record, since_sha=since_sha, lookups=lookups)
    later = cached_renames(lookups=lookups, from_sha=since_sha, to_sha=head_sha)
    unpaired = unpaired_rename(lookups=lookups, renames=later, path=path)
    moved_on = (
        later is None
        or path in later.moved
        or path in later.ambiguous
        or bool(unpaired)
    )
    read_path = (unpaired or later.moved.get(path, path)) if later is not None else path
    weak = hold == LOCATION_WEAK_RENAME or (
        later is not None and path in later.weak and path not in later.ambiguous
    )
    relocation: tuple[str, str, int, str] | None = None
    if weak:
        # Similarity alone proves no move: hold it where it was stored (#3466).
        relocation = (record.key, record.file, record.line, LOCATION_WEAK_RENAME)
    elif moved_on and read_path != record.file:
        relocation = (record.key, read_path, record.line, "")
    elif line is None and path != record.file:
        relocation = (record.key, path, record.line, "")
    elif hold:
        relocation = (record.key, record.file, record.line, hold)
    elif line is not None and record.line_sha != since_sha:
        relocation = (record.key, path, line, since_sha)
    return _Placement(
        record=record,
        read_path=read_path,
        line=None if moved_on else line,
        relocation=relocation,
    )


def _collect(*, placements: Iterable[_Placement]) -> MappedFindings:
    """Gather placements into the lines to show and the relocations.

    Args:
        placements: The round's placements, in record order.

    Returns:
        The mapped findings.
    """
    lines: dict[str, set[int] | None] = {}
    relocations: list[tuple[str, str, int, str]] = []
    for placement in placements:
        if placement.read_path and placement.line is None:
            lines[placement.read_path] = None
        elif placement.read_path and placement.line is not None:
            found = lines.setdefault(placement.read_path, set())
            if found is not None:
                found.add(placement.line)
        if placement.relocation is not None:
            relocations.append(placement.relocation)
    return MappedFindings(
        lines={
            path: None if found is None else tuple(sorted(found))
            for path, found in sorted(lines.items())
        },
        relocations=tuple(relocations),
    )


def _place(
    *,
    record: FindingRecord,
    since_sha: str,
    lookups: RoundLookups,
) -> tuple[str, int | None, str]:
    """Return a record's path and line at ``since_sha``.

    Args:
        record: An open record.
        since_sha: The head to place it at.
        lookups: The round's git answers.

    Returns:
        ``(path, line, hold)``; the line is ``None`` when it cannot be
        placed, and ``hold`` is :data:`LOCATION_UNMAPPED` when git could not
        reach the record's head and no rename metadata moved it (#3413), or
        :data:`LOCATION_WEAK_RENAME` when ``path`` is the destination of a
        low-similarity rename git paired the record's file with (#3466); the
        record is then held at its stored location.
    """
    if record.line_sha == since_sha:
        return record.file, record.line, ""
    renames = cached_renames(
        lookups=lookups,
        from_sha=record.line_sha,
        to_sha=since_sha,
    )
    if renames is None or not record.line_sha:
        # Only the record's own path follows the PR's renames (#3432).
        moved = lookups.renamed_from.get(record.file)
        if moved:
            return moved, None, ""
        return record.file, None, LOCATION_UNMAPPED if record.line_sha else ""
    dest = renames.moved.get(record.file, record.file)
    if record.file in renames.ambiguous:
        return dest, None, ""
    if record.file in renames.weak:
        return dest, None, LOCATION_WEAK_RENAME
    unpaired = unpaired_rename(lookups=lookups, renames=renames, path=record.file)
    if unpaired:
        return unpaired, None, ""
    key = (record.line_sha, since_sha, record.file, dest)
    if key not in lookups.hunks:
        lookups.hunks[key] = file_hunks(
            repo_root=lookups.repo_root,
            from_sha=record.line_sha,
            to_sha=since_sha,
            old_path=record.file,
            new_path=dest,
        )
    return dest, map_line(line=record.line, hunks=lookups.hunks[key]), ""


def shows_every_line(
    *,
    ranges: Collection[tuple[int, int]],
    lines: Collection[int] | None,
) -> bool:
    """Tell whether a delta's old-side ranges show every given line.

    Uses the matcher's rule (:func:`lintro.ai.review.finding_carry._line_unread`):
    a line is shown only inside a positive range, so a delta never shows a
    line-0 finding.

    Args:
        ranges: The delta's old-side ``(start, end)`` ranges.
        lines: Lines of the open findings on the file, or ``None`` when one
            of them could not be mapped to the delta's base.

    Returns:
        True when no open finding's line would be left unread.
    """
    if lines is None:
        return False
    return all(
        any(start <= line <= end for start, end in ranges if start > 0)
        for line in lines
    )
