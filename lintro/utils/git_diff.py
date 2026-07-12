"""Git-diff based file selection for ``--diff`` scanning.

This module resolves the set of files changed relative to a base ref so that
``lintro chk`` / ``lintro fmt`` can limit their work to the current branch's
changes instead of scanning the whole tree.

The changed set is the union of:

- Committed changes on this branch vs the base (``git diff <base>...HEAD``).
- Staged changes (``git diff --cached``).
- Unstaged working-tree changes (``git diff``).
- Untracked files (``git ls-files --others --exclude-standard``).

Deleted and rename-source paths never appear in the result: the diffs use
``--diff-filter=ACMR`` (excluding ``D``) and every candidate is finally
filtered to paths that still exist on disk, so renames and deletions cannot
produce phantom paths that would break downstream tools.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking git; all invocations use shell=False
from functools import lru_cache

# Sentinel used by the ``--diff`` CLI option to mean "flag supplied without an
# explicit base"; resolved to the repository's default base ref at runtime.
DIFF_DEFAULT_SENTINEL: str = "\x00lintro-diff-default\x00"

# Ordered fallbacks tried when resolving the default base ref.
_DEFAULT_BASE_CANDIDATES: tuple[str, ...] = (
    "origin/HEAD",
    "origin/main",
    "origin/master",
    "main",
    "master",
)

_GIT_TIMEOUT_SECONDS: float = 30.0


class DiffResolutionError(Exception):
    """Raised when an explicit ``--diff`` base ref cannot be resolved."""


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    """Run a git command capturing text output.

    Args:
        args: Git arguments (without the leading ``git``).
        cwd: Working directory to run git in.

    Returns:
        The completed process (never raises on non-zero exit).

    Raises:
        FileNotFoundError: When ``git`` is not installed or not on ``PATH``.
    """
    git_bin = shutil.which("git")
    if git_bin is None:
        raise FileNotFoundError("git is not installed or not on PATH")
    return subprocess.run(  # nosec B603 - argv is [resolved git binary, *args]; shell=False; args are git subcommands only, not user shell input
        [git_bin, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )


def is_git_repository(path: str = ".") -> bool:
    """Return whether ``path`` is inside a git working tree.

    Args:
        path: Directory to probe.

    Returns:
        True when git is available and ``path`` is inside a repository.
    """
    if shutil.which("git") is None:
        return False
    try:
        result = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def resolve_git_cwd_from_paths(paths: list[str]) -> str:
    """Derive the git working directory from explicit target paths.

    When callers pass concrete scan targets, git diff operations must run in
    that checkout's repository rather than the process cwd. The default path
    ``'.'`` keeps the existing process-cwd behavior.

    Args:
        paths: Target paths passed to file discovery.

    Returns:
        Directory to use as ``cwd`` for git diff helpers.
    """
    if paths == ["."]:
        return "."

    for path in paths:
        abs_path = os.path.abspath(path)
        probe = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)
        root = _repo_root(probe)
        if root:
            return root

    abs_paths = [os.path.abspath(path) for path in paths]
    parent_dirs = {
        path if os.path.isdir(path) else os.path.dirname(path) for path in abs_paths
    }
    if len(parent_dirs) == 1:
        return parent_dirs.pop()

    try:
        return os.path.commonpath(list(parent_dirs))
    except ValueError:
        return abs_paths[0]


def _repo_root(cwd: str) -> str | None:
    """Return the absolute repository root for ``cwd``, or None.

    Args:
        cwd: Directory inside the repository.

    Returns:
        Absolute path to the repository top level, or None when unavailable.
    """
    try:
        result = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return root or None


def ref_exists(ref: str, cwd: str = ".") -> bool:
    """Return whether ``ref`` resolves to a commit.

    Args:
        ref: Git ref or revision to verify.
        cwd: Directory inside the repository.

    Returns:
        True when the ref resolves.
    """
    try:
        result = _run_git(
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def resolve_default_base(cwd: str = ".") -> str | None:
    """Resolve the default base ref for ``--diff`` with no explicit base.

    Tries ``origin/HEAD`` (the remote's default branch), then common fallbacks.

    Args:
        cwd: Directory inside the repository.

    Returns:
        A resolvable base ref, or None when none of the candidates exist.
    """
    # Prefer the symbolic remote HEAD (e.g. resolves to ``origin/main``).
    try:
        symbolic = _run_git(
            ["rev-parse", "--abbrev-ref", "origin/HEAD"],
            cwd=cwd,
        )
        if symbolic.returncode == 0:
            candidate = symbolic.stdout.strip()
            if candidate and candidate != "origin/HEAD" and ref_exists(candidate, cwd):
                return candidate
    except (OSError, subprocess.SubprocessError):
        pass

    for candidate in _DEFAULT_BASE_CANDIDATES:
        if ref_exists(candidate, cwd):
            return candidate
    return None


def _merge_base_diff_names(base: str, cwd: str) -> list[str]:
    """Return names changed on HEAD since diverging from ``base``.

    Uses the three-dot form so only changes introduced on the current branch
    are reported, matching ``git diff <base>...HEAD``.

    Args:
        base: Base ref to diff against.
        cwd: Directory inside the repository.

    Returns:
        Repo-relative paths (rename/copy targets, no deletions).
    """
    result = _run_git(
        ["diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"],
        cwd=cwd,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _worktree_diff_names(cwd: str, *, cached: bool) -> list[str]:
    """Return staged or unstaged changed names in the working tree.

    Args:
        cwd: Directory inside the repository.
        cached: When True, report staged changes; otherwise unstaged.

    Returns:
        Repo-relative paths (no deletions).
    """
    args = ["diff", "--name-only", "--diff-filter=ACMR"]
    if cached:
        args.append("--cached")
    result = _run_git(args, cwd=cwd)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _untracked_names(cwd: str) -> list[str]:
    """Return untracked (but not ignored) file names.

    Args:
        cwd: Directory inside the repository.

    Returns:
        Repo-relative paths for untracked files.
    """
    result = _run_git(
        ["ls-files", "--others", "--exclude-standard"],
        cwd=cwd,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


@lru_cache(maxsize=32)
def get_changed_files(base: str, cwd: str = ".") -> frozenset[str]:
    """Return absolute paths of files changed vs ``base``.

    The result unions branch, staged, unstaged, and untracked changes, then
    keeps only paths that still exist on disk so renamed and deleted files do
    not leak phantom paths. Results are cached per ``(base, cwd)`` so repeated
    tool invocations in one run share a single git probe.

    Args:
        base: Resolved base ref to diff against.
        cwd: Directory inside the repository.

    Returns:
        Absolute file paths in the changed set (possibly empty).

    Raises:
        DiffResolutionError: When ``base`` does not resolve to a commit.
    """
    root = _repo_root(cwd) or os.path.abspath(cwd)

    if not ref_exists(base, cwd):
        raise DiffResolutionError(
            f"Cannot resolve --diff base ref '{base}'. Fetch it or pass an "
            f"existing ref (e.g. 'main' or 'origin/main').",
        )

    names: set[str] = set()
    names.update(_merge_base_diff_names(base, cwd))
    names.update(_worktree_diff_names(cwd, cached=True))
    names.update(_worktree_diff_names(cwd, cached=False))
    names.update(_untracked_names(cwd))

    changed: set[str] = set()
    for name in names:
        abs_path = os.path.realpath(os.path.join(root, name))
        # Drop deletions / rename sources that no longer exist on disk.
        if os.path.isfile(abs_path):
            changed.add(abs_path)
    return frozenset(changed)


def filter_files_by_diff(
    files: list[str],
    base: str,
    cwd: str = ".",
) -> list[str]:
    """Filter ``files`` to those in the diff changed set.

    Args:
        files: Absolute candidate file paths from normal discovery.
        base: Resolved base ref to diff against.
        cwd: Directory inside the repository.

    Returns:
        The subset of ``files`` that changed vs ``base`` (order preserved).
    """
    changed = get_changed_files(base, cwd)
    if not changed:
        return []
    changed_real = {os.path.realpath(path) for path in changed}
    return [f for f in files if os.path.realpath(f) in changed_real]
