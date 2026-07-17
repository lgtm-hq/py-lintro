"""File discovery and path utilities for tool plugins.

This module provides file discovery, path validation, and working directory computation.
"""

from __future__ import annotations

import os
import sys

from loguru import logger
from rich.progress import Progress, SpinnerColumn, TextColumn

from lintro.plugins.protocol import ToolDefinition
from lintro.utils.path_filtering import walk_files_with_excludes
from lintro.utils.path_utils import find_lintro_ignore

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
