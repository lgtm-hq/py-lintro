"""Which path an open finding's file now has, by the PR's renames (#3413, #3432).

:mod:`lintro.ai.review.delta_lines` places each open finding at a round's
head. When git reads a rename as a delete plus an add (a file renamed and
heavily edited in the same range), or cannot reach the finding's head at
all, the pull request's own rename metadata names the file's new path
(:func:`unpaired_rename`).

That metadata spans the whole pull request: ``a.py -> c.py`` even after an
earlier round carried the finding across ``a.py -> b.py``. A carried record
keeps the path it was first tracked at
(:attr:`~lintro.ai.review.models.finding_record.FindingRecord.origin_path`),
but it is not looked up (#3432): paths and add/delete counts cannot prove
which file a delete plus an add continues, and every guarded lookup tried
moved some live finding onto an unrelated file and resolved its thread.
Holding the finding can leave a duplicate thread; a wrong move resolves a
live one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lintro.ai.review.git_line_map import Hunks, Renames, range_renames

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "RoundLookups",
    "cached_renames",
    "unpaired_rename",
]


@dataclass
class RoundLookups:
    """Git answers one round reuses across its records.

    Attributes:
        repo_root: The repository holding the commits.
        renamed_from: The pull request's renames, source path to new path.
        renames: Renames per ``(from_sha, to_sha)`` range.
        hunks: Hunks per ``(from_sha, to_sha, old_path, new_path)``.
        rewrites: Whether git reads a file as rewritten, per ``(from_sha,
            to_sha, path)`` (#3466).
    """

    repo_root: str
    renamed_from: Mapping[str, str]
    renames: dict[tuple[str, str], Renames | None] = field(default_factory=dict)
    hunks: dict[tuple[str, str, str, str], Hunks] = field(default_factory=dict)
    rewrites: dict[tuple[str, str, str], bool] = field(default_factory=dict)


def cached_renames(
    *,
    lookups: RoundLookups,
    from_sha: str,
    to_sha: str,
) -> Renames | None:
    """Return the renames between two commits, listing each range once.

    Args:
        lookups: The round's git answers.
        from_sha: The older commit.
        to_sha: The newer commit.

    Returns:
        The renames (none for an empty range), or ``None`` when git could not
        list them.
    """
    if from_sha == to_sha:
        return Renames(moved={}, ambiguous=frozenset())
    if (from_sha, to_sha) not in lookups.renames:
        lookups.renames[(from_sha, to_sha)] = range_renames(
            repo_root=lookups.repo_root,
            from_sha=from_sha,
            to_sha=to_sha,
        )
    return lookups.renames[(from_sha, to_sha)]


def unpaired_rename(
    *,
    lookups: RoundLookups,
    renames: Renames | None,
    path: str,
) -> str:
    """Return the PR's rename destination for a path git saw deleted (#3413).

    A file renamed and heavily edited in the same range reads to git as a
    delete plus an add; the pull request's rename metadata still pairs it.

    Args:
        lookups: The round's git answers and the PR's renames.
        renames: Renames git listed for the range, or ``None``.
        path: The path at the range's start.

    Returns:
        The destination, or ``""`` when git paired the path itself or did not
        report it deleted, or the pull request names no rename for it.
    """
    if renames is None or path not in renames.deleted:
        return ""
    return lookups.renamed_from.get(path, "")
