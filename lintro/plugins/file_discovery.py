"""File discovery and path utilities for tool plugins.

This module provides file discovery, path validation, and working directory computation.
"""

from __future__ import annotations

import os
import sys

from loguru import logger
from rich.progress import Progress, SpinnerColumn, TextColumn

from lintro.plugins.protocol import ToolDefinition
from lintro.utils.execution_paths import (
    _PROJECT_ROOT_SEARCH_MAX_DEPTH as _PROJECT_ROOT_SEARCH_MAX_DEPTH,
)
from lintro.utils.execution_paths import (
    get_cwd as get_cwd,
)
from lintro.utils.execution_paths import (
    get_execution_cwd as _execution_cwd,
)
from lintro.utils.path_filtering import (
    DEFAULT_EXCLUDE_PATTERNS,
    setup_exclude_patterns,
    walk_files_with_excludes,
)

# ``DEFAULT_EXCLUDE_PATTERNS`` and ``setup_exclude_patterns`` moved to
# ``lintro.utils.path_filtering`` in #1743: the run-level verify pass needs the
# same exclude resolution a plugin's own discovery uses, and it lives in the
# tools layer, which may not import ``lintro.plugins``. Both names are
# re-exported here so every existing caller keeps working.
__all__ = [
    "DEFAULT_EXCLUDE_PATTERNS",
    "discover_files",
    "get_cwd",
    "get_execution_cwd",
    "setup_exclude_patterns",
    "validate_paths",
]


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


def get_execution_cwd(files: list[str]) -> str:
    """Anchor tool execution to the project containing its discovered files.

    Args:
        files: Files the tool will inspect.

    Returns:
        Execution directory using the shared capability-probe anchoring rule.
    """
    return _execution_cwd(files, max_depth=_PROJECT_ROOT_SEARCH_MAX_DEPTH)
