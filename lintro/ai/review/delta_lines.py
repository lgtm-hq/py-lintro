"""Which lines a delta round must show to re-judge an open finding (#3403).

A delta round (#2627) narrows a changed file to its ``since..head`` hunks. A
prior open finding whose line falls outside every hunk is then carried
unread: the matcher cannot resolve what the round did not show. When the fix
landed elsewhere in the file (tests appended below a test-gap finding, a
merge block widened below the line a logic bug was reported on), that
carry repeats every round and the finding never closes. These helpers name
the lines a delta has to show, so :func:`lintro.ai.review.delta.apply_delta_hunks`
can read such a file whole instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.review.enums.finding_status import FindingStatus

if TYPE_CHECKING:
    from collections.abc import Collection

    from lintro.ai.review.models.review_state import ReviewState

__all__ = ["open_finding_lines", "shows_every_line"]


def open_finding_lines(*, prior: ReviewState | None) -> dict[str, tuple[int, ...]]:
    """Name the lines of each open prior finding, per file.

    Every open record counts, posted inline or not: a finding shown only in
    the sticky has no thread to re-litigate it, so re-reading its line is the
    only way a later round can see that it was fixed.

    Args:
        prior: State of the previous rounds, or ``None``.

    Returns:
        Sorted unique lines per path, in the prior head's coordinates; empty
        without prior state.
    """
    if prior is None:
        return {}
    lines: dict[str, set[int]] = {}
    for record in prior.findings:
        if record.status is FindingStatus.OPEN and record.file:
            lines.setdefault(record.file, set()).add(record.line)
    return {path: tuple(sorted(found)) for path, found in sorted(lines.items())}


def shows_every_line(
    *,
    ranges: Collection[tuple[int, int]],
    lines: Collection[int],
) -> bool:
    """Tell whether a delta's old-side ranges show every given line.

    Uses the matcher's rule (:func:`lintro.ai.review.finding_carry._line_unread`):
    a line is shown only inside a positive range, so a delta never shows a
    line-0 finding.

    Args:
        ranges: The delta's old-side ``(start, end)`` ranges.
        lines: Lines of the open findings on the file.

    Returns:
        True when no open finding's line would be left unread.
    """
    return all(
        any(start <= line <= end for start, end in ranges if start > 0)
        for line in lines
    )
