"""Shared path utilities for AI display and safety checks."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

OUTSIDE_WORKSPACE_SENTINEL = "<outside-workspace>"
"""Sentinel returned by :func:`to_provider_path` for paths outside the workspace."""


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
        Workspace-relative POSIX path when under workspace_root,
        or :data:`OUTSIDE_WORKSPACE_SENTINEL` for any path outside it.
    """
    resolved = resolve_workspace_file(file_path, workspace_root)
    if resolved is None:
        return OUTSIDE_WORKSPACE_SENTINEL
    return resolved.relative_to(workspace_root.resolve()).as_posix()


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    mode: int | None = None,
    fallback_mode: int = 0o644,
) -> None:
    """Replace ``path`` with ``data`` atomically, preserving its mode.

    ``tempfile.mkstemp`` creates files as ``0600`` and :meth:`Path.replace`
    keeps that mode, so a naive write-then-rename silently strips the
    executable bit and group/other read access from every file it restores.

    Args:
        path: Destination file. Its parent must already exist.
        data: Bytes to write.
        mode: Permission bits to force. Callers restoring a snapshot pass the
            bits the file had *then*, so a mode the run itself changed is
            rolled back too. ``None`` keeps whatever ``path`` currently has.
        fallback_mode: Permission bits to apply when ``mode`` is ``None`` and
            ``path`` does not exist yet.

    Raises:
        BaseException: Re-raised after the partial temporary file is removed,
            so a failed write never leaves debris beside the target.
    """
    if mode is None:
        mode = (
            path.stat().st_mode & 0o7777
            if path.is_file() and not path.is_symlink()
            else fallback_mode
        )
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".lintro-tmp")
    try:
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            handle.write(data)
        os.chmod(tmp, mode)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
