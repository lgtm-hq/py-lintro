"""Path filtering and file discovery utilities.

Functions for filtering paths, walking directories, and excluding files based on
patterns. Uses pathspec library for gitignore-style pattern matching.
"""

import fnmatch
import os
from functools import lru_cache
from typing import TYPE_CHECKING

import pathspec

if TYPE_CHECKING:
    pass


@lru_cache(maxsize=32)
def _compile_pathspec(patterns_tuple: tuple[str, ...]) -> pathspec.GitIgnoreSpec:
    """Compile patterns into a GitIgnoreSpec object (cached).

    Args:
        patterns_tuple: Tuple of gitignore-style patterns to compile.

    Returns:
        pathspec.GitIgnoreSpec: Compiled pattern matcher.
    """
    return pathspec.GitIgnoreSpec.from_lines(patterns_tuple)


def should_exclude_path(
    path: str,
    exclude_patterns: list[str],
) -> bool:
    """Check if a path should be excluded based on patterns.

    Uses pathspec library for gitignore-style pattern matching, which provides
    better support for complex patterns like ** globs and directory matching.

    Args:
        path: str: File path to check for exclusion (can be absolute or relative).
        exclude_patterns: list[str]: List of gitignore-style patterns to match against.

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

    # Normalize path separators for cross-platform compatibility
    normalized_path: str = abs_path.replace("\\", "/")

    # Convert patterns list to tuple for caching
    patterns_tuple = tuple(p.strip() for p in exclude_patterns if p.strip())

    if not patterns_tuple:
        return False

    # Compile patterns using pathspec (with caching)
    spec = _compile_pathspec(patterns_tuple)

    # Check if the full path matches
    if spec.match_file(normalized_path):
        return True

    # Also check relative parts of the path for directory patterns
    # This handles patterns like "build" matching "/path/to/build/file.py"
    path_parts = normalized_path.split("/")
    for i in range(len(path_parts)):
        relative_part = "/".join(path_parts[i:])
        if relative_part and spec.match_file(relative_part):
            return True

    return False


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

    for path in paths:
        if os.path.isfile(path):
            # Single file - check if the filename matches any file pattern
            filename = os.path.basename(path)
            for pattern in file_patterns:
                if fnmatch.fnmatch(filename, pattern):
                    abs_path = os.path.abspath(path)
                    if not _should_exclude_with_spec(abs_path, exclude_spec):
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
) -> bool:
    """Check if a path should be excluded using a pre-compiled PathSpec.

    Args:
        path: Absolute file path to check.
        spec: Pre-compiled PathSpec, or None if no exclusions.

    Returns:
        bool: True if the path should be excluded.
    """
    if spec is None:
        return False

    normalized = path.replace("\\", "/")

    if spec.match_file(normalized):
        return True

    # Check relative parts for directory pattern matching
    path_parts = normalized.split("/")
    for i in range(len(path_parts)):
        relative = "/".join(path_parts[i:])
        if relative and spec.match_file(relative):
            return True

    return False


def _is_venv_directory(dirname: str) -> bool:
    """Check if a directory name indicates a virtual environment.

    Args:
        dirname: str: Directory name to check.

    Returns:
        bool: True if the directory appears to be a virtual environment.
    """
    from lintro.utils.tool_utils import VENV_PATTERNS

    return dirname in VENV_PATTERNS
