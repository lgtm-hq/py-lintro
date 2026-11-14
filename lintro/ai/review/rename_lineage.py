"""Which path an open finding's file now has, by the PR's renames (#3413, #3432).

:mod:`lintro.ai.review.delta_lines` places each open finding at a round's
head. When git reads a rename as a delete plus an add (a file renamed and
heavily edited in the same range), or cannot reach the finding's head at
all, the pull request's own rename metadata names the file's new path
(:func:`unpaired_rename`).

That metadata spans the whole pull request: ``a.py -> c.py`` even after an
earlier round carried the finding across ``a.py -> b.py``. A carried record
therefore keeps the path it was first tracked at
(:attr:`~lintro.ai.review.models.finding_record.FindingRecord.origin_path`),
and :func:`lineage_rename` looks that path up, but only across a range git
can walk commit by commit. The lookup is skipped whenever the first path may
now be a different file, git pairs the destination with another source, or
the range's history is out of reach: holding the finding can leave a
duplicate thread, while a wrong move can resolve a live one.

A finding first reported on a file the pull request had already renamed
never moves, so it would keep no first path. :func:`stamp_base_paths` gives
such a new record the path its file had at the pull request's base instead
(#3449), and the same guarded lookup applies to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from lintro.ai.review.git_line_map import (
    Hunks,
    Renames,
    path_added_in_range,
    path_exists,
    range_renames,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lintro.ai.review.models.finding_match_result import FindingMatchResult

__all__ = [
    "RoundLookups",
    "cached_exists",
    "cached_renames",
    "lineage_rename",
    "stamp_base_paths",
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
    to_sha: str,
) -> str:
    """Return the PR's rename of a record's first path, when unambiguous (#3432).

    The lookup needs the range's own history: with no listing (the record's
    head is unreachable) nothing shows that the first path still names the
    record's file, so the record is held as before. It is also skipped
    whenever the first path may now be a different file: it exists at the
    range's start, the range's endpoints name it, or any commit in the range
    adds it (re-created, or another file renamed onto it, even when that file
    moved on again before the range's end). And it is skipped when git pairs
    the destination with another source in the range, since that file's own
    findings arrive there.

    Args:
        lookups: The round's git answers and the PR's renames.
        renames: Renames git listed for the range, or ``None`` when git could
            not list them.
        path: The record's path at the range's start.
        origin_path: The record's first path, or ``""``.
        from_sha: The range's start.
        to_sha: The range's end.

    Returns:
        The destination, or ``""`` when there is none or it is ambiguous.
    """
    dest = lookups.renamed_from.get(origin_path, "") if origin_path else ""
    if not dest or dest == path or renames is None:
        return ""
    if (
        path not in renames.deleted
        or origin_path in renames.named
        or dest in renames.moved.values()
    ):
        return ""
    if cached_exists(lookups=lookups, sha=from_sha, path=origin_path) is not False:
        return ""
    added = path_added_in_range(
        repo_root=lookups.repo_root,
        from_sha=from_sha,
        to_sha=to_sha,
        path=origin_path,
    )
    return dest if added is False else ""


def stamp_base_paths(
    *,
    match: FindingMatchResult,
    renamed_from: Mapping[str, str],
) -> FindingMatchResult:
    """Give each new record on a renamed file its path at the PR's base (#3449).

    A record first created on ``b.py`` while the pull request renames
    ``a.py -> b.py`` has not moved, so it would keep no
    :attr:`~lintro.ai.review.models.finding_record.FindingRecord.origin_path`.
    When ``b.py`` is later renamed again and git reads that as a delete plus
    an add, the pull request's metadata names only ``a.py -> c.py``; recording
    ``a.py`` lets :func:`lineage_rename` look it up, under every one of its
    guards. Carried and regressed records keep the lineage they already have.

    Args:
        match: The round's matching result.
        renamed_from: The pull request's renames, source path to new path.

    Returns:
        The result with ``origin_path`` set on every new record whose file
        the pull request renamed; unchanged when there is none.
    """
    base_of = {new: old for old, new in renamed_from.items() if old and new != old}
    stamped = {
        record.key: replace(record, origin_path=base_of[record.file])
        for record in match.new
        if not record.origin_path and record.file in base_of
    }
    if not stamped:
        return match
    return replace(
        match,
        records=tuple(stamped.get(record.key, record) for record in match.records),
        new=tuple(stamped.get(record.key, record) for record in match.new),
    )
