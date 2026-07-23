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


#: Markers that identify a project root, probed in order in each directory
#: while walking up from the discovered files. ``.git`` covers repositories
#: (a directory normally, a file in worktrees/submodules, so existence — not
#: is-dir — is what matters); the rest cover language projects. The *nearest*
#: marker wins, so a file in a nested project (e.g. a monorepo package with its
#: own ``package.json``/``tsconfig.json``) anchors to that project — the
#: directory a tool's own config discovery expects — rather than the outer repo.
_PROJECT_ROOT_MARKERS: tuple[str, ...] = (
    ".git",
    "pyproject.toml",
    "package.json",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "Cargo.toml",
)

#: Bounds the upward marker search, counting the files' common ancestor itself,
#: so an unrelated marker from a far filesystem ancestor (e.g. a dotfile-managed
#: ``~/.git``, or a distant vendored ``package.json``) is never picked up.
#: Matches ``_IGNORE_SEARCH_MAX_DEPTH`` used for ``.lintro-ignore`` discovery.
_PROJECT_ROOT_SEARCH_MAX_DEPTH = 20


def get_execution_cwd(files: list[str]) -> str:
    """Return a stable, scope-independent working directory for tool subprocesses.

    The tool subprocess is anchored to the **project root** of the discovered
    files — the nearest ancestor directory holding a project marker (``.git``,
    ``pyproject.toml``, ``package.json``, ...), found by walking up from the
    files' common ancestor.

    Unlike :func:`get_cwd` (the raw common ancestor), this does not move with
    the input scope: within one project, walking up from a single file's
    directory or from the whole-repo common ancestor reaches the *same* marker,
    so the path a tool sees for a given file — and thus any config ``overrides``
    keyed on that path — is identical whether the user passed a file, a
    directory, or ``.`` (#1616). Because the anchor is derived from the files
    (not the process cwd) and uses the *nearest* marker, each tool's own config
    discovery still resolves against the project the files actually belong to,
    including nested projects in a monorepo.

    Falls back to the files' common ancestor when no marker is found within
    ``_PROJECT_ROOT_SEARCH_MAX_DEPTH`` ancestors (preserving prior behavior for
    marker-less trees, and avoiding a distant unrelated marker), and to the
    process cwd when there are no files.

    Note:
        When a single invocation spans multiple nested projects, the common
        ancestor (and thus the anchor) is the outer project; per-project
        anchoring in that case would require grouping files by project root and
        running the tool once per group.

    Args:
        files: Discovered file paths the tool will process.

    Returns:
        Absolute path to use as the tool subprocess working directory.
    """
    common = get_cwd(files)
    if common is None:
        return os.getcwd()
    marker = find_file_upward(
        Path(common),
        _PROJECT_ROOT_MARKERS,
        max_depth=_PROJECT_ROOT_SEARCH_MAX_DEPTH,
    )
    if marker is not None:
        return str(marker.parent)
    return common
