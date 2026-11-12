"""Which lines a delta round must show to re-judge an open finding (#3403).

A delta round (#2627) narrows a changed file to its ``since..head`` hunks. A
prior open finding whose line falls outside every hunk is then carried
unread: the matcher cannot resolve what the round did not show. When the fix
landed elsewhere in the file (tests appended below a test-gap finding, a
merge block widened below the line a logic bug was reported on), that
carry repeats every round and the finding never closes. These helpers name
the lines a delta has to show, so :func:`lintro.ai.review.delta.apply_delta_hunks`
can read such a file whole instead.

A record's line is in the coordinates of the head that last reported it
(:attr:`~lintro.ai.review.models.finding_record.FindingRecord.line_sha`). A
round that carried it unread did not move it, so before the delta check the
line is mapped forward to the delta's base through ``line_sha..since``. When
that is not possible (no recorded head, git cannot diff the range, or the
line itself was edited) the file is read whole: failing open here is exactly
the false "fixed" #3403 is about.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.exceptions import ReviewContextError

if TYPE_CHECKING:
    from collections.abc import Collection

    from lintro.ai.review.models.review_state import ReviewState

__all__ = ["open_finding_lines", "shows_every_line"]

#: A unified-diff hunk header: old start/length, new length.
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@")

#: ``(old_start, old_length, new_length)`` per hunk, or ``None`` when unknown.
_Hunks = tuple[tuple[int, int, int], ...] | None


def open_finding_lines(
    *,
    prior: ReviewState | None,
    since_sha: str,
    repo_root: str,
) -> dict[str, tuple[int, ...] | None]:
    """Name the lines of each open prior finding, per file, at ``since_sha``.

    Every open record counts, posted inline or not: a finding shown only in
    the sticky has no thread to re-litigate it, so re-reading its line is the
    only way a later round can see that it was fixed.

    Args:
        prior: State of the previous rounds, or ``None``.
        since_sha: The delta's base, the prior round's head.
        repo_root: The repository holding both commits.

    Returns:
        Sorted unique lines per path in ``since_sha``'s coordinates, or
        ``None`` for a path holding a finding whose line could not be mapped
        there (the caller reads that file whole); empty without prior state.
    """
    if prior is None:
        return {}
    lines: dict[str, set[int] | None] = {}
    hunks_by_range: dict[tuple[str, str], _Hunks] = {}
    for record in prior.findings:
        if record.status is not FindingStatus.OPEN or not record.file:
            continue
        mapped: int | None = record.line
        if record.line_sha != since_sha:
            key = (record.line_sha, record.file)
            if key not in hunks_by_range:
                hunks_by_range[key] = _range_hunks(
                    repo_root=repo_root,
                    from_sha=record.line_sha,
                    to_sha=since_sha,
                    path=record.file,
                )
            mapped = _map_line(line=record.line, hunks=hunks_by_range[key])
        found = lines.setdefault(record.file, set())
        if found is None:
            continue
        if mapped is None:
            lines[record.file] = None
        else:
            found.add(mapped)
    return {
        path: None if found is None else tuple(sorted(found))
        for path, found in sorted(lines.items())
    }


def _range_hunks(*, repo_root: str, from_sha: str, to_sha: str, path: str) -> _Hunks:
    """Return the hunks of ``from_sha..to_sha`` for one file.

    Args:
        repo_root: The repository holding both commits.
        from_sha: The head the line was recorded at; empty when unknown.
        to_sha: The head to map it to.
        path: The file.

    Returns:
        The hunks (empty when the file did not change), or ``None`` when the
        range is unknown or git could not compute it.
    """
    if not from_sha or not to_sha:
        return None
    try:
        result = _run_git(
            args=[
                "-C",
                repo_root,
                "diff",
                "--no-color",
                "--no-ext-diff",
                "-U0",
                f"{from_sha}..{to_sha}",
                "--",
                path,
            ],
            check=False,
        )
    except ReviewContextError as exc:
        logger.debug("Could not map finding lines on {}: {}", path, exc)
        return None
    if result.returncode != 0:
        return None
    hunks: list[tuple[int, int, int]] = []
    for text in result.stdout.splitlines():
        match = _HUNK_RE.match(text)
        if match is not None:
            old_len, new_len = match.group(2), match.group(3)
            hunks.append(
                (
                    int(match.group(1)),
                    1 if old_len is None else int(old_len),
                    1 if new_len is None else int(new_len),
                ),
            )
    # Output without a hunk header (a binary change) gives nothing to map by.
    if result.stdout.strip() and not hunks:
        return None
    return tuple(hunks)


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
