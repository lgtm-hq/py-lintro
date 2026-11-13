"""Pinned git diffs that map a line between two commits (#3403, #3413).

:mod:`lintro.ai.review.delta_lines` places each open finding at a round's
head; these are the git primitives it uses. Every diff here pins its format
so user configuration cannot change the coordinates: literal pathspecs (a
path such as ``odd[1].py`` is never a glob), no text conversion or external
driver, no colour, no relative paths and fixed ``a/``/``b/`` prefixes, and
only the section whose headers name the intended file is read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from lintro.ai.review.context.diff_parse import (
    _unquote_git_path,
    split_unified_diff_by_file,
)
from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.exceptions import ReviewContextError

__all__ = [
    "PINNED_DIFF_FLAGS",
    "Hunks",
    "Renames",
    "file_hunks",
    "git_text",
    "literal_pathspec",
    "map_line",
    "path_added_in_range",
    "path_exists",
    "range_renames",
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
Hunks = tuple[tuple[int, int, int], ...] | None


@dataclass(frozen=True, slots=True)
class Renames:
    """Renames git reports between two commits.

    Attributes:
        moved: Source path to destination path.
        ambiguous: Source paths git also reports as touched another way
            (renamed away and re-added, say), so no single destination holds
            the finding.
        deleted: Paths git reports as deleted; a rename it did not pair
            (heavily edited on the way) shows up here (#3413).
        named: Every path the range names, rename and copy sources
            included, so a caller can tell a path the range never touched
            (#3432).
    """

    moved: dict[str, str]
    ambiguous: frozenset[str]
    deleted: frozenset[str] = frozenset()
    named: frozenset[str] = frozenset()


def literal_pathspec(path: str) -> str:
    """Return a pathspec that matches ``path`` exactly, never as a glob.

    Args:
        path: Repository-relative path.

    Returns:
        The ``:(literal)`` pathspec.
    """
    return f":(literal){path}"


def git_text(*, repo_root: str, args: list[str]) -> str | None:
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


def range_renames(*, repo_root: str, from_sha: str, to_sha: str) -> Renames | None:
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
    text = git_text(
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
    deleted: set[str] = set()
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index]
        if status.startswith(("R", "C")) and index + 2 < len(tokens):
            moved[tokens[index + 1]] = tokens[index + 2]
            touched.append(tokens[index + 2])
            index += 3
        else:
            touched.extend(tokens[index + 1 : index + 2])
            if status == "D" and index + 1 < len(tokens):
                deleted.add(tokens[index + 1])
            index += 2
    return Renames(
        moved=moved,
        ambiguous=frozenset(source for source in moved if source in touched),
        deleted=frozenset(deleted),
        named=frozenset((*moved, *touched)),
    )


def path_added_in_range(
    *,
    repo_root: str,
    from_sha: str,
    to_sha: str,
    path: str,
) -> bool | None:
    """Tell whether any commit in a range adds a path (#3432).

    Compares every commit, not the range's endpoints, so a file created
    and renamed away again within the range still counts. Rename detection
    is off, so a rename onto the path is reported as adding it, and merge
    commits are compared with each parent.

    Args:
        repo_root: The repository.
        from_sha: The older commit (excluded).
        to_sha: The newer commit.
        path: Repository-relative path.

    Returns:
        Whether some commit adds the path, or ``None`` when git could not
        walk the range or a shallow clone may not hold all of it
        (:func:`_range_is_complete`).
    """
    if not from_sha or not to_sha or not path:
        return None
    try:
        if not _range_is_complete(
            repo_root=repo_root,
            from_sha=from_sha,
            to_sha=to_sha,
        ):
            return None
        result = _run_git(
            args=[
                "-C",
                repo_root,
                "-c",
                "core.quotePath=false",
                "log",
                "--format=",
                "--name-only",
                "-z",
                "-m",
                "--no-renames",
                "--diff-filter=A",
                "--no-ext-diff",
                "--no-textconv",
                "--no-relative",
                f"{from_sha}..{to_sha}",
                "--",
                literal_pathspec(path),
            ],
            check=False,
        )
    except ReviewContextError as exc:
        logger.debug("Could not walk {}..{} for {}: {}", from_sha, to_sha, path, exc)
        return None
    if result.returncode != 0:
        return None
    return path in result.stdout.split("\0")


def _range_is_complete(*, repo_root: str, from_sha: str, to_sha: str) -> bool:
    """Tell whether the repository holds every commit of ``from_sha..to_sha``.

    A shallow clone answers ``git log`` over a range cut short by its
    boundary without an error: both ends can be readable while the commits
    between them were never fetched. In a shallow repository the range is
    complete only when ``from_sha`` is an ancestor of ``to_sha`` there and no
    shallow boundary commit lies inside the range (#3432).

    Args:
        repo_root: The repository.
        from_sha: The older commit (excluded).
        to_sha: The newer commit.

    Returns:
        True when every commit is present; False when one may be missing or
        git could not tell. A failure to run git at all propagates to
        :func:`path_added_in_range`, which reports it as unknown.
    """
    shallow = _run_git(
        args=["-C", repo_root, "rev-parse", "--is-shallow-repository"],
        check=False,
    )
    if shallow.returncode != 0:
        return False
    if shallow.stdout.strip() != "true":
        return True
    ancestor = _run_git(
        args=["-C", repo_root, "merge-base", "--is-ancestor", from_sha, to_sha],
        check=False,
    )
    shallow_file = _run_git(
        args=["-C", repo_root, "rev-parse", "--git-path", "shallow"],
        check=False,
    )
    walked = _run_git(
        args=["-C", repo_root, "rev-list", f"{from_sha}..{to_sha}"],
        check=False,
    )
    if (
        ancestor.returncode != 0
        or shallow_file.returncode != 0
        or walked.returncode != 0
    ):
        return False
    try:
        # ``--git-path`` answers relative to the directory git ran in.
        shallow_path = Path(repo_root) / shallow_file.stdout.strip()
        boundary = set(shallow_path.read_text().split())
    except OSError as exc:
        logger.debug("Could not read the shallow boundary: {}", exc)
        return False
    return boundary.isdisjoint(walked.stdout.split())


def path_exists(*, repo_root: str, sha: str, path: str) -> bool | None:
    """Tell whether a commit's tree holds a path (#3432).

    Args:
        repo_root: The repository.
        sha: The commit.
        path: Repository-relative path.

    Returns:
        Whether the path exists at ``sha``, or ``None`` when git could not
        answer (no commit given, a commit the repository does not hold, or
        git failed to run).
    """
    if not sha or not path:
        return None
    try:
        found = _run_git(
            args=["-C", repo_root, "cat-file", "-e", f"{sha}:{path}"],
            check=False,
        )
        if found.returncode == 0:
            return True
        # The same exit status covers a missing path and a missing commit;
        # only a tree git can read makes the path's absence an answer.
        tree = _run_git(
            args=["-C", repo_root, "cat-file", "-e", f"{sha}^{{tree}}"],
            check=False,
        )
    except ReviewContextError as exc:
        logger.debug("Could not look up {} at {}: {}", path, sha, exc)
        return None
    return False if tree.returncode == 0 else None


def file_hunks(
    *,
    repo_root: str,
    from_sha: str,
    to_sha: str,
    old_path: str,
    new_path: str,
) -> Hunks:
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
    text = git_text(
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


def map_line(*, line: int, hunks: Hunks) -> int | None:
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
