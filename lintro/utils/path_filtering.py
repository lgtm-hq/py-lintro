"""Path filtering and file discovery utilities.

Functions for filtering paths, walking directories, and excluding files based on
patterns. Uses pathspec library for gitignore-style pattern matching.
"""

import fnmatch
import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pathspec

if TYPE_CHECKING:
    from collections.abc import Sequence

# Files/directories that mark the root of a project. Exclude patterns are
# gitignore-style and therefore anchored at the project root, not at the
# filesystem root — see ``resolve_exclude_anchors`` (#1678).
PROJECT_ROOT_MARKERS: tuple[str, ...] = (
    ".lintro-ignore",
    ".lintro-config.yaml",
    ".lintro-config.yml",
    "pyproject.toml",
    "package.json",
    ".git",
)

# Bound the upward search for a project root so a far filesystem ancestor is
# never treated as the anchor. Mirrors the ``.lintro-ignore`` search bound.
_ROOT_SEARCH_MAX_DEPTH: int = 20


@lru_cache(maxsize=32)
def _compile_pathspec(patterns_tuple: tuple[str, ...]) -> pathspec.GitIgnoreSpec:
    """Compile patterns into a GitIgnoreSpec object (cached).

    Args:
        patterns_tuple: Tuple of gitignore-style patterns to compile.

    Returns:
        pathspec.GitIgnoreSpec: Compiled pattern matcher.
    """
    return pathspec.GitIgnoreSpec.from_lines(patterns_tuple)


def find_project_root(start: str | Path | None = None) -> str | None:
    """Locate the project root by walking up for a project marker.

    Args:
        start: Directory to begin searching from. Defaults to the current
            working directory.

    Returns:
        Absolute path of the directory containing the first marker found, or
        None when no marker exists within the bounded search.
    """
    from lintro.utils.path_utils import find_file_upward

    begin = Path(start) if start is not None else Path.cwd()
    try:
        begin = begin.absolute()
    except OSError:  # pragma: no cover - defensive
        return None

    found = find_file_upward(
        begin,
        PROJECT_ROOT_MARKERS,
        max_depth=_ROOT_SEARCH_MAX_DEPTH,
    )
    if found is None:
        return None
    return str(found.parent)


def resolve_exclude_anchors(paths: "Sequence[str] | None" = None) -> tuple[str, ...]:
    """Return the directories exclude patterns are interpreted relative to.

    Exclude patterns (``.lintro-ignore`` entries and the built-in defaults)
    use gitignore semantics, which are relative to the project root. Matching
    them against the *absolute* path instead means any ancestor directory
    **above** the project — for example a checkout living under
    ``<repo>/.claude/worktrees/<id>`` or under ``~/build`` — silently excludes
    every file in the project and every tool then reports a clean pass on a
    scan that never happened (#1678).

    Args:
        paths: Input paths for the current scan. Directory inputs — and the
            project root of any file input — act as fallback anchors for trees
            outside the current project (temporary directories, sibling
            checkouts). They are only consulted when the current project root
            does not contain the file, so exclusions inside the project are
            unaffected.

    Returns:
        Anchor directories in preference order, project root first.
    """
    anchors: list[str] = []

    project_root = find_project_root()
    if project_root is not None:
        anchors.append(project_root)

    file_roots: list[str] = []
    seen_parents: set[str] = set()
    for path in paths or ():
        try:
            abs_path = os.path.abspath(path)
        except (ValueError, OSError):  # pragma: no cover - defensive
            continue
        if os.path.isdir(abs_path):
            if abs_path not in anchors:
                anchors.append(abs_path)
            continue
        # A file named outside the current project still belongs to *some*
        # project; anchor on its own root rather than the filesystem root.
        parent = os.path.dirname(abs_path)
        if not parent or parent in seen_parents:
            continue
        seen_parents.add(parent)
        file_root = find_project_root(parent)
        if file_root is not None and file_root not in file_roots:
            file_roots.append(file_root)

    anchors.extend(root for root in file_roots if root not in anchors)

    if not anchors:
        anchors.append(str(Path.cwd()))

    return tuple(anchors)


def _match_candidates(path: str, anchors: tuple[str, ...]) -> list[str]:
    """Build the path forms an exclude spec should be matched against.

    For a file inside one of the anchors, the candidates are the
    anchor-relative path and its sub-path suffixes. Suffixes keep patterns
    such as ``test_samples/*`` matching at any depth inside the project, while
    anchoring keeps directories *above* the project — ``<repo>/.claude/…``,
    ``~/build/…`` — from excluding the entire scan (#1678).

    A file outside every anchor falls back to legacy whole-path matching.

    Args:
        path: Absolute file path to check.
        anchors: Anchor directories in preference order.

    Returns:
        Candidate path strings to test against the compiled spec.
    """
    normalized = path.replace("\\", "/")

    for anchor in anchors:
        try:
            relative = os.path.relpath(path, anchor)
        except (ValueError, OSError):  # pragma: no cover - cross-drive paths
            continue
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            continue
        return _path_suffixes(relative.replace("\\", "/"))

    return _path_suffixes(normalized)


def _path_suffixes(path: str) -> list[str]:
    """Return a path plus every sub-path suffix of it.

    Args:
        path: Slash-separated path.

    Returns:
        The path itself followed by each suffix starting at a later component.
    """
    parts = [part for part in path.split("/") if part]
    return ["/".join(parts[i:]) for i in range(len(parts))]


def should_exclude_path(
    path: str,
    exclude_patterns: list[str],
    anchors: tuple[str, ...] | None = None,
) -> bool:
    """Check if a path should be excluded based on patterns.

    Uses pathspec library for gitignore-style pattern matching, which provides
    better support for complex patterns like ** globs and directory matching.
    Matching is anchored at the project root so directories above it cannot
    exclude the whole project (#1678).

    Args:
        path: str: File path to check for exclusion (can be absolute or relative).
        exclude_patterns: list[str]: List of gitignore-style patterns to match against.
        anchors: Anchor directories to interpret patterns relative to. Defaults
            to the resolved project root (falling back to the cwd).

    Returns:
        bool: True if the path should be excluded, False otherwise.
    """
    if not exclude_patterns:
        return False

    # Normalize to absolute path for consistent comparison
    try:
        abs_path = os.path.abspath(path)
    except (ValueError, OSError):
        abs_path = path

    # Convert patterns list to tuple for caching
    patterns_tuple = tuple(p.strip() for p in exclude_patterns if p.strip())

    if not patterns_tuple:
        return False

    # Compile patterns using pathspec (with caching)
    spec = _compile_pathspec(patterns_tuple)

    return _should_exclude_with_spec(
        abs_path,
        spec,
        anchors if anchors is not None else resolve_exclude_anchors([path]),
    )


def walk_files_with_excludes(
    paths: list[str],
    file_patterns: list[str],
    exclude_patterns: list[str],
    include_venv: bool = False,
    incremental: bool = False,
    tool_name: str | None = None,
    diff_base: str | None = None,
) -> list[str]:
    """Return files under ``paths`` matching patterns and not excluded.

    Uses pathspec for gitignore-style exclude pattern matching.

    Args:
        paths: Files or directories to search.
        file_patterns: Glob patterns to include (fnmatch-style).
        exclude_patterns: Gitignore-style patterns to exclude.
        include_venv: Include virtual environment directories when True.
        incremental: If True, only return files changed since last run.
        tool_name: Tool name for incremental cache (required if incremental=True).
        diff_base: Resolved git base ref. When set, the result is restricted to
            files changed relative to this ref (``git diff <base>...HEAD`` plus
            working-tree and untracked changes).

    Returns:
        Sorted file paths matching include filters and not excluded.
    """
    all_files: list[str] = []

    # Pre-compile exclude patterns for efficiency
    exclude_tuple = tuple(p.strip() for p in exclude_patterns if p.strip())
    exclude_spec = _compile_pathspec(exclude_tuple) if exclude_tuple else None
    # Anchor gitignore-style patterns at the project root so ancestors above
    # it cannot silently exclude the entire scan (#1678).
    anchors = resolve_exclude_anchors(paths)

    for path in paths:
        if os.path.isfile(path):
            # Single file - check if the filename matches any file pattern
            filename = os.path.basename(path)
            for pattern in file_patterns:
                if fnmatch.fnmatch(filename, pattern):
                    abs_path = os.path.abspath(path)
                    if not _should_exclude_with_spec(
                        abs_path,
                        exclude_spec,
                        anchors,
                    ):
                        all_files.append(abs_path)
                    break
        elif os.path.isdir(path):
            # Directory - walk through it
            for root, dirs, files in os.walk(path):
                # Filter out virtual environment directories unless include_venv is True
                if not include_venv:
                    dirs[:] = [d for d in dirs if not _is_venv_directory(d)]

                # Check each file against the patterns
                for file in files:
                    file_path: str = os.path.join(root, file)
                    abs_file_path: str = os.path.abspath(file_path)

                    # Check if file matches any file pattern
                    matches_pattern: bool = False
                    for pattern in file_patterns:
                        if fnmatch.fnmatch(file, pattern):
                            matches_pattern = True
                            break

                    if matches_pattern and not _should_exclude_with_spec(
                        abs_file_path,
                        exclude_spec,
                        anchors,
                    ):
                        all_files.append(abs_file_path)

    # Apply git-diff filtering if a base ref was resolved. Restricts the set to
    # files changed relative to the base so only branch changes are scanned.
    if diff_base:
        from lintro.utils.git_diff import filter_files_by_diff_for_paths

        all_files = filter_files_by_diff_for_paths(
            all_files,
            diff_base,
            paths,
        )

    # Apply incremental filtering if enabled
    if incremental and tool_name:
        from lintro.utils.file_cache import ToolCache

        cache = ToolCache.load(tool_name)
        changed_files = cache.get_changed_files(all_files)

        # Update cache with all discovered files for next run
        cache.update(all_files)
        cache.save()

        return sorted(changed_files)

    return sorted(all_files)


def _should_exclude_with_spec(
    path: str,
    spec: pathspec.GitIgnoreSpec | None,
    anchors: tuple[str, ...],
) -> bool:
    """Check if a path should be excluded using a pre-compiled PathSpec.

    Args:
        path: Absolute file path to check.
        spec: Pre-compiled PathSpec, or None if no exclusions.
        anchors: Anchor directories the patterns are relative to.

    Returns:
        bool: True if the path should be excluded.
    """
    if spec is None:
        return False

    return any(
        candidate and spec.match_file(candidate)
        for candidate in _match_candidates(path, anchors)
    )


def _is_venv_directory(dirname: str) -> bool:
    """Check if a directory name indicates a virtual environment.

    Args:
        dirname: str: Directory name to check.

    Returns:
        bool: True if the directory appears to be a virtual environment.
    """
    from lintro.utils.tool_utils import VENV_PATTERNS

    return dirname in VENV_PATTERNS
