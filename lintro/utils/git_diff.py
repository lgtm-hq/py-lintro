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


def _ref_exists(ref: str, cwd: str) -> bool:
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
            if candidate and candidate != "origin/HEAD" and _ref_exists(candidate, cwd):
                return candidate
    except (OSError, subprocess.SubprocessError):
        pass

    for candidate in _DEFAULT_BASE_CANDIDATES:
        if _ref_exists(candidate, cwd):
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

    if not _ref_exists(base, cwd):
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
        abs_path = os.path.abspath(os.path.join(root, name))
        # Drop deletions / rename sources that no longer exist on disk.
        if os.path.isfile(abs_path):
            changed.add(abs_path)
    return frozenset(changed)


def _probe_path_for_repo(path: str) -> str:
    """Return a directory to probe for a git repository root.

    Args:
        path: File or directory scan target.

    Returns:
        Directory path suitable for ``git rev-parse``.
    """
    abs_path = os.path.abspath(path)
    if os.path.isfile(abs_path):
        return os.path.dirname(abs_path) or abs_path
    if os.path.isdir(abs_path):
        return abs_path
    parent = os.path.dirname(abs_path)
    return parent if parent else "."


def _path_in_repo(path: str, repo_root: str) -> bool:
    """Return whether ``path`` lies inside ``repo_root``.

    Args:
        path: Candidate file path.
        repo_root: Absolute repository root.

    Returns:
        True when ``path`` is the root itself or a path beneath it.
    """
    abs_path = os.path.abspath(path)
    abs_root = os.path.abspath(repo_root)
    return abs_path == abs_root or abs_path.startswith(abs_root + os.sep)


def _files_under_scan_path(files: list[str], scan_path: str) -> list[str]:
    """Return discovered files that fall under a scan target path.

    Args:
        files: Absolute candidate file paths from discovery.
        scan_path: Original scan target (file or directory).

    Returns:
        Files contained in or equal to ``scan_path``.
    """
    abs_scan = os.path.abspath(scan_path)
    if os.path.isfile(abs_scan):
        return [f for f in files if os.path.abspath(f) == abs_scan]
    prefix = abs_scan if abs_scan.endswith(os.sep) else abs_scan + os.sep
    return [
        f
        for f in files
        if os.path.abspath(f).startswith(prefix)
        or os.path.abspath(f) == abs_scan.rstrip(os.sep)
    ]


def resolve_git_cwd_from_paths(
    paths: list[str],
) -> dict[str | None, list[str]]:
    """Group scan targets by their git repository root.

    Each path is mapped to the repository that contains it. Paths outside any
    git working tree are grouped under ``None``.

    Args:
        paths: Files or directories the user asked to scan.

    Returns:
        Mapping from repository root (``None`` for non-repo paths) to the scan
        targets belonging to that group.
    """
    groups: dict[str | None, list[str]] = {}
    for path in paths:
        probe = _probe_path_for_repo(path)
        root = _repo_root(probe)
        groups.setdefault(root, []).append(path)
    return groups


def _resolve_base_for_repo(base: str, repo_root: str) -> str | None:
    """Resolve the diff base ref for a single repository.

    Args:
        base: Explicit base ref or :data:`DIFF_DEFAULT_SENTINEL`.
        repo_root: Repository root directory.

    Returns:
        Resolved base ref, or ``None`` when the default base cannot be found.
    """
    if base == DIFF_DEFAULT_SENTINEL:
        return resolve_default_base(repo_root)
    return base


def _validate_explicit_base_for_paths(base: str, scan_paths: list[str]) -> None:
    """Ensure an explicit base ref resolves in every grouped repository.

    Args:
        base: Explicit base ref (not :data:`DIFF_DEFAULT_SENTINEL`).
        scan_paths: Files or directories the user asked to scan.

    Raises:
        DiffResolutionError: When ``base`` does not resolve in a grouped
            repository.
    """
    if base == DIFF_DEFAULT_SENTINEL:
        return
    groups = resolve_git_cwd_from_paths(scan_paths)
    for repo_root in groups:
        if repo_root is None:
            continue
        if not _ref_exists(base, repo_root):
            raise DiffResolutionError(
                f"Cannot resolve --diff base ref '{base}'. Fetch it or pass an "
                f"existing ref (e.g. 'main' or 'origin/main').",
            )


def get_changed_files_for_paths(
    base: str,
    scan_paths: list[str],
) -> frozenset[str]:
    """Return changed files unioned across repositories in ``scan_paths``.

    Each repository group is diffed independently using the same base-ref
    semantics as :func:`get_changed_files`. Paths outside any git repository
    are ignored because they do not participate in diff filtering.

    Args:
        base: Explicit base ref or :data:`DIFF_DEFAULT_SENTINEL`.
        scan_paths: Files or directories the user asked to scan.

    Returns:
        Absolute paths changed in any grouped repository.
    """
    _validate_explicit_base_for_paths(base, scan_paths)
    groups = resolve_git_cwd_from_paths(scan_paths)
    changed: set[str] = set()
    for repo_root in groups:
        if repo_root is None:
            continue
        resolved_base = _resolve_base_for_repo(base, repo_root)
        if resolved_base is None:
            continue
        changed.update(get_changed_files(resolved_base, repo_root))
    return frozenset(changed)


def filter_files_by_diff_for_paths(
    files: list[str],
    base: str,
    scan_paths: list[str],
) -> list[str]:
    """Filter ``files`` using per-repository diff resolution.

    Scan targets are grouped by repository root. Each group is diffed
    independently and only files under that group's targets are considered.
    Paths outside any git repository keep all discovered files (no diff
    filtering), matching the non-repo fallback used by the CLI.

    Args:
        files: Absolute candidate file paths from normal discovery.
        base: Explicit base ref or :data:`DIFF_DEFAULT_SENTINEL`.
        scan_paths: Files or directories the user asked to scan.

    Returns:
        The subset of ``files`` that changed vs ``base`` (order preserved).
    """
    _validate_explicit_base_for_paths(base, scan_paths)
    groups = resolve_git_cwd_from_paths(scan_paths)
    included: set[str] = set()

    for repo_root, group_paths in groups.items():
        if repo_root is None:
            for scan_path in group_paths:
                for file_path in _files_under_scan_path(files, scan_path):
                    included.add(os.path.abspath(file_path))
            continue

        resolved_base = _resolve_base_for_repo(base, repo_root)
        if resolved_base is None:
            for scan_path in group_paths:
                for file_path in _files_under_scan_path(files, scan_path):
                    abs_file = os.path.abspath(file_path)
                    if _path_in_repo(abs_file, repo_root):
                        included.add(abs_file)
            continue

        repo_files = [f for f in files if _path_in_repo(f, repo_root)]
        for file_path in filter_files_by_diff(repo_files, resolved_base, repo_root):
            included.add(os.path.abspath(file_path))

    return [f for f in files if os.path.abspath(f) in included]


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
    return [f for f in files if os.path.abspath(f) in changed]
