"""File discovery and path utilities for tool plugins.

This module provides file discovery, path validation, and working directory computation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger
from rich.progress import Progress, SpinnerColumn, TextColumn

from lintro.plugins.protocol import ToolDefinition
from lintro.utils.path_filtering import walk_files_with_excludes
from lintro.utils.path_utils import find_file_upward, find_lintro_ignore

# Default exclude patterns for file discovery
DEFAULT_EXCLUDE_PATTERNS: list[str] = [
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*cache*",
    ".coverage",
    "htmlcov",
    "dist",
    "build",
    "*.egg-info",
]


def setup_exclude_patterns(
    exclude_patterns: list[str],
) -> list[str]:
    """Set up exclude patterns with defaults and .lintro-ignore.

    Args:
        exclude_patterns: Current exclude patterns to extend.

    Returns:
        Updated list of exclude patterns.
    """
    patterns = list(exclude_patterns)

    # Add default exclude patterns
    for pattern in DEFAULT_EXCLUDE_PATTERNS:
        if pattern not in patterns:
            patterns.append(pattern)

    # Add .lintro-ignore patterns if present
    try:
        lintro_ignore_path = find_lintro_ignore()
        if lintro_ignore_path and lintro_ignore_path.exists():
            with open(lintro_ignore_path, encoding="utf-8") as f:
                for line in f:
                    line_stripped = line.strip()
                    if not line_stripped or line_stripped.startswith("#"):
                        continue
                    if line_stripped not in patterns:
                        patterns.append(line_stripped)
    except (OSError, UnicodeDecodeError) as e:
        logger.debug(f"Could not read .lintro-ignore: {e}")

    return patterns


def discover_files(
    paths: list[str],
    definition: ToolDefinition,
    exclude_patterns: list[str],
    include_venv: bool = False,
    show_progress: bool = True,
    diff_base: str | None = None,
    incremental: bool = False,
) -> list[str]:
    """Discover files matching the tool's patterns.

    Args:
        paths: Input paths to search.
        definition: Tool definition with file patterns.
        exclude_patterns: Patterns to exclude.
        include_venv: Whether to include virtual environment files.
        show_progress: Whether to show a progress spinner during discovery.
        diff_base: Resolved git base ref. When set, restricts discovery to files
            changed relative to this ref.
        incremental: When True, restrict discovery to files changed since the
            last run using the per-tool fingerprint cache. The tool name is
            taken from ``definition.name`` for the cache key.

    Returns:
        List of matching file paths.
    """
    # Disable progress when not in a TTY or when show_progress is False
    disable_progress = not show_progress or not sys.stdout.isatty()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        disable=disable_progress,
    ) as progress:
        task = progress.add_task("Discovering files...", total=None)
        files = walk_files_with_excludes(
            paths=paths,
            file_patterns=definition.file_patterns,
            exclude_patterns=exclude_patterns,
            include_venv=include_venv,
            incremental=incremental,
            tool_name=definition.name,
            diff_base=diff_base,
        )
        progress.update(task, description=f"Found {len(files)} files")

    logger.debug(
        f"File discovery: {len(files)} files matching {definition.file_patterns}",
    )
    return files


def validate_paths(paths: list[str]) -> None:
    """Validate that paths exist and are accessible.

    Args:
        paths: Paths to validate.

    Raises:
        FileNotFoundError: If any path does not exist.
        PermissionError: If any path is not accessible.
    """
    for path in paths:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Path does not exist: {path}")
        if not os.access(path, os.R_OK):
            raise PermissionError(f"Path is not accessible: {path}")


def get_cwd(paths: list[str]) -> str | None:
    """Get common parent directory for paths.

    Args:
        paths: Paths to compute common parent for.

    Returns:
        Common parent directory path, or None if not applicable.
    """
    if not paths:
        return None

    # Get the parent directory for each path
    # For files: use dirname; for directories: use the path itself
    parent_dirs: set[str] = set()
    for p in paths:
        abs_path = os.path.abspath(p)
        if os.path.isdir(abs_path):
            parent_dirs.add(abs_path)
        else:
            parent_dirs.add(os.path.dirname(abs_path))

    if len(parent_dirs) == 1:
        return parent_dirs.pop()

    try:
        return os.path.commonpath(list(parent_dirs))
    except ValueError:
        # Can happen on Windows with paths on different drives
        return None


#: Git marker, preferred as the anchor: a repository has a single root, so it
#: stays fixed even when nested language-project markers (monorepo packages)
#: sit between a file and the repo root. ``.git`` is a directory in a normal
#: checkout and a file in worktrees/submodules, so existence (not is-dir) is
#: what matters.
_GIT_MARKER: tuple[str, ...] = (".git",)

#: Language/project markers used only when there is no enclosing git repository.
_LANG_ROOT_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "Cargo.toml",
)


def get_execution_cwd(files: list[str]) -> str:
    """Return a stable, scope-independent working directory for tool subprocesses.

    The tool subprocess is anchored to the **project root** of the discovered
    files, found by walking up from the files' common ancestor. The git
    repository root (``.git``) is preferred; only when there is no enclosing
    repository is the nearest language-project marker (``pyproject.toml``,
    ``package.json``, ...) used.

    Unlike :func:`get_cwd` (the raw common ancestor), this does not move with
    the input scope: walking up from a single file's directory or from the
    whole-repo common ancestor reaches the *same* repository root, so the path a
    tool sees for a given file — and thus any config ``overrides`` keyed on that
    path — is identical whether the user passed a file, a directory, or ``.``
    (#1616). Preferring the git root keeps this stable in a monorepo, where a
    narrow invocation would otherwise stop at a nested package marker while a
    repo-wide invocation stops at the outer one. Because the anchor is derived
    from the files (not the process cwd), each tool's own config discovery still
    resolves relative to where the files actually live.

    Falls back to the files' common ancestor when no marker is found (preserving
    prior behavior for marker-less trees), and to the process cwd when there are
    no files.

    Args:
        files: Discovered file paths the tool will process.

    Returns:
        Absolute path to use as the tool subprocess working directory.
    """
    common = get_cwd(files)
    if common is None:
        return os.getcwd()
    start = Path(common)
    git_root = find_file_upward(start, _GIT_MARKER)
    if git_root is not None:
        return str(git_root.parent)
    lang_root = find_file_upward(start, _LANG_ROOT_MARKERS)
    if lang_root is not None:
        return str(lang_root.parent)
    return common
