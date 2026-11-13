"""Where each open finding sits at a delta round's base (#3403).

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
following a rename git reports. The mapped location is also what the matcher
judges the finding at (:attr:`MappedFindings.relocations`). When a location
cannot be mapped (no recorded head, git cannot diff the range, the line
itself was edited, or a rename is ambiguous) the file is read whole: failing
open here is exactly the false "fixed" #3403 is about.

Every diff here pins its format so user configuration cannot change the
coordinates: literal pathspecs (a path such as ``odd[1].py`` is never a
glob), no text conversion or external driver, no colour, no relative paths
and fixed ``a/``/``b/`` prefixes, and only the section whose headers name
the intended file is read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.review.context.diff_parse import (
    _unquote_git_path,
    split_unified_diff_by_file,
)
from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.exceptions import ReviewContextError

if TYPE_CHECKING:
    from collections.abc import Collection

    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "PINNED_DIFF_FLAGS",
    "MappedFindings",
    "literal_pathspec",
    "map_open_findings",
    "shows_every_line",
]

#: Flags that fix a diff's text whatever the user's git configuration says.
PINNED_DIFF_FLAGS: tuple[str, ...] = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-relative",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)

#: A unified-diff hunk header: old start/length, new length.
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@", re.MULTILINE)

#: The old-side file header of a diff section.
_OLD_HEADER_RE = re.compile(r'^--- (?:"((?:[^"\\]|\\.)+)"|(.+?))(?:\t|$)', re.MULTILINE)

#: ``(old_start, old_length, new_length)`` per hunk, or ``None`` when unknown.
_Hunks = tuple[tuple[int, int, int], ...] | None


@dataclass(frozen=True, slots=True)
class _Renames:
    """Renames git reports between two commits.

    Attributes:
        moved: Source path to destination path.
        ambiguous: Source paths git also reports as touched another way
            (renamed away and re-added, say), so no single destination holds
            the finding.
    """

    moved: dict[str, str]
    ambiguous: frozenset[str]


@dataclass(frozen=True, slots=True)
class MappedFindings:
    """The open findings' locations at a delta round's base.

    Attributes:
        lines: Lines per file a narrowed file must show, in the base's
            coordinates; ``None`` for a file that must be read whole because
            a finding on it could not be placed there.
        relocations: ``(key, path, line, line_sha)`` for each open record
            whose stored location was not already in the base's coordinates.
            ``line_sha`` is the base when the line was mapped, and empty when
            only the path is known (a rename whose line could not be placed),
            which makes the next round read the file whole again.
    """

    lines: dict[str, tuple[int, ...] | None] = field(default_factory=dict)
    relocations: tuple[tuple[str, str, int, str], ...] = ()


def literal_pathspec(path: str) -> str:
    """Return a pathspec that matches ``path`` exactly, never as a glob.

    Args:
        path: Repository-relative path.

    Returns:
        The ``:(literal)`` pathspec.
    """
    return f":(literal){path}"


def map_open_findings(
    *,
    prior: ReviewState | None,
    since_sha: str,
    head_sha: str,
    repo_root: str,
) -> MappedFindings:
    """Place every open prior finding at ``since_sha`` (#3403).

    Every open record counts, posted inline or not: a finding shown only in
    the sticky has no thread to re-litigate it, so re-reading its line is the
    only way a later round can see that it was fixed.

    Args:
        prior: State of the previous rounds, or ``None``.
        since_sha: The delta's base, the prior round's head.
        head_sha: This round's head; a file renamed since the base is keyed
            by its head path, whose chunk the round reads.
        repo_root: The repository holding the commits.

    Returns:
        The mapped lines and the relocations to judge findings at.
    """
    if prior is None:
        return MappedFindings()
    cache: dict[tuple[str, str], _Renames | None] = {}
    lines: dict[str, set[int] | None] = {}
    relocations: list[tuple[str, str, int, str]] = []
    for record in prior.findings:
        if record.status is not FindingStatus.OPEN or not record.file:
            continue
        path, line = _place(
            record=record,
            since_sha=since_sha,
            renames=_cached_renames(
                cache=cache,
                repo_root=repo_root,
                from_sha=record.line_sha,
                to_sha=since_sha,
            ),
            repo_root=repo_root,
        )
        later = _cached_renames(
            cache=cache,
            repo_root=repo_root,
            from_sha=since_sha,
            to_sha=head_sha,
        )
        moved_on = later is None or path in later.moved or path in later.ambiguous
        read_path = later.moved.get(path, path) if later is not None else path
        if line is None or moved_on:
            lines[read_path] = None
        else:
            found = lines.setdefault(read_path, set())
            if found is not None:
                found.add(line)
        if moved_on and read_path != record.file:
            relocations.append((record.key, read_path, record.line, ""))
        elif line is None and path != record.file:
            relocations.append((record.key, path, record.line, ""))
        elif line is not None and record.line_sha != since_sha:
            relocations.append((record.key, path, line, since_sha))
    return MappedFindings(
        lines={
            path: None if found is None else tuple(sorted(found))
            for path, found in sorted(lines.items())
        },
        relocations=tuple(relocations),
    )


def _cached_renames(
    *,
    cache: dict[tuple[str, str], _Renames | None],
    repo_root: str,
    from_sha: str,
    to_sha: str,
) -> _Renames | None:
    """Return the renames between two commits, listing each range once.

    Args:
        cache: Ranges already listed this round.
        repo_root: The repository.
        from_sha: The older commit.
        to_sha: The newer commit.

    Returns:
        The renames (none for an empty range), or ``None`` when git could not
        list them.
    """
    if from_sha == to_sha:
        return _Renames(moved={}, ambiguous=frozenset())
    if (from_sha, to_sha) not in cache:
        cache[(from_sha, to_sha)] = _range_renames(
            repo_root=repo_root,
            from_sha=from_sha,
            to_sha=to_sha,
        )
    return cache[(from_sha, to_sha)]


def _place(
    *,
    record: FindingRecord,
    since_sha: str,
    renames: _Renames | None,
    repo_root: str,
) -> tuple[str, int | None]:
    """Return a record's path and line at ``since_sha``.

    Args:
        record: An open record.
        since_sha: The head to place it at.
        renames: Renames between the record's head and ``since_sha``, or
            ``None`` when git could not list them.
        repo_root: The repository holding the commits.

    Returns:
        ``(path, line)``; the line is ``None`` when it cannot be placed.
    """
    if record.line_sha == since_sha:
        return record.file, record.line
    if renames is None or not record.line_sha:
        return record.file, None
    dest = renames.moved.get(record.file, record.file)
    if record.file in renames.ambiguous:
        return dest, None
    hunks = _file_hunks(
        repo_root=repo_root,
        from_sha=record.line_sha,
        to_sha=since_sha,
        old_path=record.file,
        new_path=dest,
    )
    return dest, _map_line(line=record.line, hunks=hunks)


def _git_text(*, repo_root: str, args: list[str]) -> str | None:
    """Run one pinned ``git diff`` and return its output.

    Args:
        repo_root: The repository.
        args: Arguments after ``git diff`` and the pinned flags.

    Returns:
        Standard output, or ``None`` when git failed.
    """
    try:
        result = _run_git(
            args=[
                "-C",
                repo_root,
                "-c",
                "core.quotePath=false",
                "diff",
                *PINNED_DIFF_FLAGS,
                *args,
            ],
            check=False,
        )
    except ReviewContextError as exc:
        logger.debug("Could not map finding locations: {}", exc)
        return None
    return result.stdout if result.returncode == 0 else None


def _range_renames(*, repo_root: str, from_sha: str, to_sha: str) -> _Renames | None:
    """List the renames git detects between two commits.

    Args:
        repo_root: The repository.
        from_sha: The older commit.
        to_sha: The newer commit.

    Returns:
        The renames, or ``None`` when git could not list them.
    """
    if not from_sha or not to_sha:
        return None
    text = _git_text(
        repo_root=repo_root,
        # Breaking rewrites lets git pair a file renamed away and then
        # re-added under its old name, which is then reported as ambiguous.
        args=[
            "--name-status",
            "-z",
            "--find-renames",
            "--break-rewrites",
            f"{from_sha}..{to_sha}",
        ],
    )
    if text is None:
        return None
    tokens = text.split("\0")
    moved: dict[str, str] = {}
    touched: list[str] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index]
        if status.startswith(("R", "C")) and index + 2 < len(tokens):
            moved[tokens[index + 1]] = tokens[index + 2]
            touched.append(tokens[index + 2])
            index += 3
        else:
            touched.extend(tokens[index + 1 : index + 2])
            index += 2
    return _Renames(
        moved=moved,
        ambiguous=frozenset(source for source in moved if source in touched),
    )


def _file_hunks(
    *,
    repo_root: str,
    from_sha: str,
    to_sha: str,
    old_path: str,
    new_path: str,
) -> _Hunks:
    """Return the hunks taking ``old_path`` at one commit to ``new_path``.

    Args:
        repo_root: The repository holding both commits.
        from_sha: The head the line was recorded at.
        to_sha: The head to map it to.
        old_path: The file at ``from_sha``.
        new_path: The file at ``to_sha`` (``old_path`` unless renamed).

    Returns:
        The hunks (empty when the file did not change), or ``None`` when git
        could not compute the range, the change is binary, or the section
        git printed is not the one from ``old_path`` to ``new_path``.
    """
    renamed = new_path != old_path
    text = _git_text(
        repo_root=repo_root,
        args=[
            "-U0",
            "--find-renames" if renamed else "--no-renames",
            f"{from_sha}..{to_sha}",
            "--",
            literal_pathspec(old_path),
            *([literal_pathspec(new_path)] if renamed else []),
        ],
    )
    if text is None:
        return None
    if not text.strip():
        return ()
    sections = split_unified_diff_by_file(unified_diff=text)
    section = sections.get(new_path)
    if section is None or not _old_side_is(section=section, path=old_path):
        return None
    if "\nBinary files " in section or section.startswith("Binary files "):
        return None
    return tuple(
        (
            int(match.group(1)),
            1 if match.group(2) is None else int(match.group(2)),
            1 if match.group(3) is None else int(match.group(3)),
        )
        for match in _HUNK_RE.finditer(section)
    )


def _old_side_is(*, section: str, path: str) -> bool:
    """Tell whether a diff section's old side is ``a/<path>``.

    Args:
        section: One file's diff section.
        path: The expected old-side path.

    Returns:
        True when the ``---`` header names ``path``. A section without one
        (a pure rename or mode change) only matches through its
        ``diff --git`` header, which :func:`split_unified_diff_by_file`
        already keyed it by.
    """
    match = _OLD_HEADER_RE.search(section)
    if match is None:
        return "\n@@ " not in section and not section.startswith("@@ ")
    quoted, plain = match.groups()
    raw = _unquote_git_path(path=quoted) if quoted is not None else (plain or "")
    return raw == f"a/{path}"


def _map_line(*, line: int, hunks: _Hunks) -> int | None:
    """Map a line through a diff's hunks to the new side.

    Args:
        line: The line on the old side.
        hunks: The diff's hunks, or ``None`` when they are unknown.

    Returns:
        The line on the new side, or ``None`` when the hunks are unknown or
        the line itself was edited or removed.
    """
    if hunks is None:
        return None
    shift = 0
    for old_start, old_len, new_len in hunks:
        if old_len == 0:
            # A pure insertion lands after line ``old_start``.
            if old_start < line:
                shift += new_len
            continue
        old_end = old_start + old_len - 1
        if old_start <= line <= old_end:
            return None
        if old_end < line:
            shift += new_len - old_len
    return line + shift


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
