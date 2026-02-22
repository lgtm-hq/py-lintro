"""Shared path utilities for AI display and safety checks."""

from __future__ import annotations

import os
from pathlib import Path


def relative_path(file_path: str) -> str:
    """Convert a path to be relative to cwd for display.

    Used by display, fix, and interactive modules to show short,
    readable paths instead of absolute ones.

    Args:
        file_path: Absolute or relative file path.

    Returns:
        Relative path string, or the original if conversion fails.
    """
    try:
        return os.path.relpath(file_path)
    except ValueError:
        return file_path


def resolve_workspace_root(config_path: str | None = None) -> Path:
    """Resolve the workspace root used for AI file operations.

    Args:
        config_path: Optional path to lintro config file.

    Returns:
        Absolute workspace root path.
    """
    if config_path:
        return Path(config_path).resolve().parent
    return Path.cwd().resolve()


def resolve_workspace_file(file_path: str, workspace_root: Path) -> Path | None:
    """Resolve a file path and ensure it stays within the workspace root.

    Args:
        file_path: Absolute or relative file path.
        workspace_root: Absolute workspace root.

    Returns:
        Resolved path if inside workspace root, else None.
    """
    if not file_path:
        return None

    root = workspace_root.resolve()
    candidate = Path(file_path)

    try:
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root / candidate).resolve()
        )
    except OSError:
        return None

    try:
        resolved.relative_to(root)
    except ValueError:
        return None

    return resolved


def to_provider_path(file_path: str, workspace_root: Path) -> str:
    """Convert file paths to provider-safe workspace-relative form.

    Args:
        file_path: Absolute or relative file path.
        workspace_root: Absolute workspace root.

    Returns:
        Workspace-relative path, or file name fallback when outside root.
    """
    resolved = resolve_workspace_file(file_path, workspace_root)
    if resolved is None:
        name = Path(file_path).name
        return name if name else "<outside-workspace>"
    return str(resolved.relative_to(workspace_root.resolve()))
