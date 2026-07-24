"""AI fix undo/rollback via git checkpoints or file snapshots.

Prefer git checkpoint refs (:mod:`lintro.ai.checkpoints`) when available.
Outside a usable git work tree, fall back to in-memory/on-disk file content
snapshots (and the legacy reverse patch under ``.lintro-cache/ai``).
"""

from __future__ import annotations

import difflib
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger

from lintro.ai.checkpoints import (
    DEFAULT_CHECKPOINT_RETENTION,
    Checkpoint,
    capture_checkpoint,
    diff_checkpoint,
    git_checkpoints_available,
    restore_checkpoint,
)
from lintro.ai.paths import resolve_workspace_file

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion

UNDO_DIR = ".lintro-cache/ai"
UNDO_FILE = "last_fixes.patch"


@dataclass
class FileSnapshot:
    """Content snapshot for non-git rollback.

    Attributes:
        root: Workspace root used when the snapshot was taken.
        contents: Map of repo-/workspace-relative path to file bytes, or
            ``None`` when the path did not exist at capture time.
    """

    root: Path
    contents: dict[str, bytes | None] = field(default_factory=dict)


@dataclass
class UndoState:
    """Rollback handle for one AI fix (or fmt) mutation batch.

    Attributes:
        kind: ``git`` when a checkpoint ref was captured, else ``file``.
        checkpoint: Git checkpoint (when ``kind == "git"``).
        file_snapshot: File-content fallback (when ``kind == "file"``).
        patch_path: Optional legacy reverse-patch path for compatibility.
        paths: Target paths recorded at prepare time.
    """

    kind: Literal["git", "file"]
    checkpoint: Checkpoint | None = None
    file_snapshot: FileSnapshot | None = None
    patch_path: Path | None = None
    paths: tuple[str, ...] = field(default_factory=tuple)


def save_undo_patch(
    suggestions: list[AIFixSuggestion],
    workspace_root: Path,
) -> Path | None:
    """Save a combined reverse patch before applying fixes.

    The patch reverses applied changes (suggested -> original) so that
    running ``git apply <patch>`` restores the original code.

    Args:
        suggestions: List of fix suggestions about to be applied.
        workspace_root: Project root directory.

    Returns:
        Path to the saved patch file, or None if nothing to save.

    Raises:
        BaseException: Re-raised after cleaning up the temporary file on write failure.
    """
    if not suggestions:
        return None
    patch_lines: list[str] = []
    for s in suggestions:
        # Ensure trailing newlines for valid unified diff output
        suggested = s.suggested_code or ""
        if suggested and not suggested.endswith("\n"):
            suggested += "\n"
        original = s.original_code or ""
        if original and not original.endswith("\n"):
            original += "\n"
        # Reverse diff: suggested -> original (for undo)
        diff = difflib.unified_diff(
            suggested.splitlines(keepends=True),
            original.splitlines(keepends=True),
            fromfile=f"a/{s.file}",
            tofile=f"b/{s.file}",
        )
        patch_lines.extend(diff)
    if not patch_lines:
        return None
    undo_dir = workspace_root / UNDO_DIR
    undo_dir.mkdir(parents=True, exist_ok=True)
    patch_path = undo_dir / UNDO_FILE
    # Atomic write: temp file + os.replace to avoid partial writes
    fd, tmp = tempfile.mkstemp(dir=undo_dir, suffix=".tmp")
    try:
        try:
            fobj = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with fobj:
            fobj.write("".join(patch_lines))
        Path(tmp).replace(patch_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return patch_path


def _suggestion_paths(suggestions: list[AIFixSuggestion]) -> list[str]:
    """Return unique suggestion file paths in stable order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for suggestion in suggestions:
        path = suggestion.file
        if not path or path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def _capture_file_snapshot(
    paths: list[str],
    *,
    workspace_root: Path,
) -> FileSnapshot:
    """Read current file bytes for fallback rollback."""
    contents: dict[str, bytes | None] = {}
    for path in paths:
        resolved = resolve_workspace_file(path, workspace_root)
        if resolved is None:
            contents[path] = None
            continue
        if resolved.is_file():
            contents[path] = resolved.read_bytes()
        else:
            contents[path] = None
    return FileSnapshot(root=workspace_root, contents=contents)


def _restore_file_snapshot(
    snapshot: FileSnapshot,
    paths: list[str] | None = None,
) -> None:
    """Restore files from a :class:`FileSnapshot`."""
    items = (
        snapshot.contents.items()
        if paths is None
        else ((p, snapshot.contents.get(p)) for p in paths)
    )
    for path, data in items:
        resolved = resolve_workspace_file(path, snapshot.root)
        if resolved is None:
            # Fall back to joining under root when the path was relative.
            candidate = Path(path)
            resolved = (
                candidate if candidate.is_absolute() else snapshot.root / candidate
            )
        if data is None:
            if resolved.is_file():
                resolved.unlink()
            continue
        resolved.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(resolved.parent), suffix=".tmp")
        try:
            try:
                fobj = os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)
                raise
            with fobj:
                fobj.write(data)
            Path(tmp).replace(resolved)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def prepare_fix_batch(
    suggestions: list[AIFixSuggestion],
    workspace_root: Path,
    *,
    retention: int = DEFAULT_CHECKPOINT_RETENTION,
) -> UndoState | None:
    """Capture rollback state before an AI fix batch mutates files.

    Prefers a git checkpoint ref. When git checkpoints are unavailable,
    falls back to a file-content snapshot plus the legacy reverse patch.

    Args:
        suggestions: Fixes about to be applied.
        workspace_root: Project root directory.
        retention: Max git checkpoint refs to keep (default 10).

    Returns:
        Undo state handle, or None when there is nothing to capture.
    """
    if not suggestions:
        return None
    paths = _suggestion_paths(suggestions)
    if not paths:
        return None

    if git_checkpoints_available(workspace_root):
        checkpoint = capture_checkpoint(
            paths,
            workspace_root=workspace_root,
            keep=retention,
        )
        if checkpoint is not None:
            # Keep legacy patch for tooling that still reads it.
            patch_path = save_undo_patch(suggestions, workspace_root)
            return UndoState(
                kind="git",
                checkpoint=checkpoint,
                patch_path=patch_path,
                paths=tuple(paths),
            )
        logger.debug("Git checkpoint unavailable; using file-snapshot fallback")

    snapshot = _capture_file_snapshot(paths, workspace_root=workspace_root)
    patch_path = save_undo_patch(suggestions, workspace_root)
    return UndoState(
        kind="file",
        file_snapshot=snapshot,
        patch_path=patch_path,
        paths=tuple(paths),
    )


def prepare_path_batch(
    paths: list[str] | tuple[str, ...],
    workspace_root: Path,
    *,
    retention: int = DEFAULT_CHECKPOINT_RETENTION,
) -> UndoState | None:
    """Capture rollback state for arbitrary paths (e.g. ``lintro fmt``).

    Args:
        paths: Files or directories about to be mutated.
        workspace_root: Project root directory.
        retention: Max git checkpoint refs to keep.

    Returns:
        Undo state handle, or None when capture is not possible / empty.
    """
    path_list = [p for p in paths if p]
    if not path_list:
        return None
    if git_checkpoints_available(workspace_root):
        checkpoint = capture_checkpoint(
            path_list,
            workspace_root=workspace_root,
            keep=retention,
        )
        if checkpoint is not None:
            return UndoState(
                kind="git",
                checkpoint=checkpoint,
                paths=checkpoint.paths,
            )
    snapshot = _capture_file_snapshot(path_list, workspace_root=workspace_root)
    if not snapshot.contents:
        return None
    return UndoState(
        kind="file",
        file_snapshot=snapshot,
        paths=tuple(snapshot.contents.keys()),
    )


def restore_undo(
    state: UndoState,
    paths: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Restore paths from a previously prepared :class:`UndoState`.

    Args:
        state: Handle from :func:`prepare_fix_batch` or
            :func:`prepare_path_batch`.
        paths: Optional subset to restore; defaults to all recorded paths.
    """
    path_list = list(paths) if paths is not None else None
    if state.kind == "git" and state.checkpoint is not None:
        restore_checkpoint(state.checkpoint, path_list)
        return
    if state.file_snapshot is not None:
        _restore_file_snapshot(state.file_snapshot, path_list)


def diff_undo(
    state: UndoState,
    paths: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Return a unified diff of current files vs the prepared undo state.

    For git checkpoints this is ``git diff <ref> -- <paths>``. For file
    snapshots a simple unified diff per path is produced.

    Args:
        state: Handle from prepare helpers.
        paths: Optional path subset.

    Returns:
        Unified diff text (empty when unchanged or unavailable).
    """
    if state.kind == "git" and state.checkpoint is not None:
        return diff_checkpoint(state.checkpoint, paths)

    if state.file_snapshot is None:
        return ""
    snapshot = state.file_snapshot
    targets = list(paths) if paths is not None else list(snapshot.contents)
    chunks: list[str] = []
    for path in targets:
        original = snapshot.contents.get(path)
        resolved = resolve_workspace_file(path, snapshot.root)
        current: bytes | None
        if resolved is not None and resolved.is_file():
            current = resolved.read_bytes()
        else:
            current = None
        if original == current:
            continue
        old_text = (
            original.decode("utf-8", errors="replace").splitlines(keepends=True)
            if original is not None
            else []
        )
        new_text = (
            current.decode("utf-8", errors="replace").splitlines(keepends=True)
            if current is not None
            else []
        )
        chunks.extend(
            difflib.unified_diff(
                old_text,
                new_text,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            ),
        )
    return "".join(chunks)


__all__ = [
    "UNDO_DIR",
    "UNDO_FILE",
    "FileSnapshot",
    "UndoState",
    "diff_undo",
    "prepare_fix_batch",
    "prepare_path_batch",
    "restore_undo",
    "save_undo_patch",
]
