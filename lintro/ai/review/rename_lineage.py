"""Which path an open finding's file now has, by the PR's renames (#3413, #3432).

:mod:`lintro.ai.review.delta_lines` places each open finding at a round's
head. When git reads a rename as a delete plus an add (a file renamed and
heavily edited in the same range), or cannot reach the finding's head at
all, the pull request's own rename metadata names the file's new path.

That metadata spans the whole pull request: ``a.py -> c.py`` even after an
earlier round carried the finding across ``a.py -> b.py``. A carried record
therefore keeps the path it was first tracked at
(:attr:`~lintro.ai.review.models.finding_record.FindingRecord.origin_path`),
and :func:`lineage_rename` looks that path up. The lookup is skipped whenever
the first path may now be a different file or git pairs the destination with
another source: holding the finding can leave a duplicate thread, while a
wrong move can resolve a live one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lintro.ai.review.git_line_map import (
    Hunks,
    Renames,
    path_exists,
    range_renames,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "RoundLookups",
    "cached_exists",
    "cached_renames",
    "lineage_rename",
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
        exists: Whether a path exists, per ``(sha, path)`` (#3432).
    """

    repo_root: str
    renamed_from: Mapping[str, str]
    renames: dict[tuple[str, str], Renames | None] = field(default_factory=dict)
    hunks: dict[tuple[str, str, str, str], Hunks] = field(default_factory=dict)
    exists: dict[tuple[str, str], bool | None] = field(default_factory=dict)


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


def cached_exists(*, lookups: RoundLookups, sha: str, path: str) -> bool | None:
    """Tell whether a path exists at a commit, asking git once per pair.

    Args:
        lookups: The round's git answers.
        sha: The commit.
        path: Repository-relative path.

    Returns:
        Whether it exists, or ``None`` when git could not answer.
    """
    if (sha, path) not in lookups.exists:
        lookups.exists[(sha, path)] = path_exists(
            repo_root=lookups.repo_root,
            sha=sha,
            path=path,
        )
    return lookups.exists[(sha, path)]


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


def lineage_rename(
    *,
    lookups: RoundLookups,
    renames: Renames | None,
    path: str,
    origin_path: str,
    from_sha: str,
) -> str:
    """Return the PR's rename of a record's first path, when unambiguous (#3432).

    The lookup is skipped, and the record held as before, whenever the first
    path may now be a different file: it exists at the range's start, or the
    range names it (re-created, or renamed within the range itself). It is
    also skipped when git pairs the destination with another source in the
    range, since that file's own findings arrive there.

    Args:
        lookups: The round's git answers and the PR's renames.
        renames: Renames git listed for the range, or ``None`` when git could
            not list them (the record's head is unreachable).
        path: The record's path at the range's start.
        origin_path: The record's first path, or ``""``.
        from_sha: The range's start; with no listing, the head the record is
            placed at.

    Returns:
        The destination, or ``""`` when there is none or it is ambiguous.
    """
    dest = lookups.renamed_from.get(origin_path, "") if origin_path else ""
    if not dest or dest == path:
        return ""
    if renames is None:
        # With no listing, the record's own path has to be gone for certain.
        if cached_exists(lookups=lookups, sha=from_sha, path=path) is not False:
            return ""
    elif (
        path not in renames.deleted
        or origin_path in renames.named
        or dest in renames.moved.values()
    ):
        return ""
    if cached_exists(lookups=lookups, sha=from_sha, path=origin_path) is not False:
        return ""
    return dest
