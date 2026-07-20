"""Apply AI-generated fix suggestions to source files.

Handles line-targeted replacement within a configurable search radius.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from loguru import logger

from lintro.ai.checkpoints import DEFAULT_CHECKPOINT_RETENTION
from lintro.ai.models import AIFixSuggestion
from lintro.ai.paths import resolve_workspace_file
from lintro.ai.undo import UndoState, prepare_fix_batch, restore_undo


def _apply_fix(
    suggestion: AIFixSuggestion,
    *,
    workspace_root: Path,
    auto_apply: bool = False,
    search_radius: int = 5,
) -> bool:
    """Apply a single fix suggestion to the file.

    Uses line-number-targeted replacement to avoid matching the wrong
    occurrence when the same code pattern appears elsewhere in the file.
    If the original code is not found within the search radius of the
    target line, the fix fails (returns False).

    Args:
        suggestion: Fix suggestion to apply.
        workspace_root: Root directory limiting writable paths.
        auto_apply: Reserved for future use; kept for API compatibility.
        search_radius: Max lines above/below the target line to search
            for the original code pattern.

    Returns:
        True if the fix was applied successfully.

    Raises:
        BaseException: Re-raised after cleanup when the atomic write fails.
    """
    try:
        resolved = resolve_workspace_file(suggestion.file, workspace_root)
        if resolved is None:
            return False
        path = resolved
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        original_lines = suggestion.original_code.splitlines(keepends=True)
        if not original_lines:
            return False

        # Ensure last line has consistent newline for comparison
        if original_lines and not original_lines[-1].endswith("\n"):
            original_lines[-1] += "\n"

        # Validate line number before doing arithmetic.
        if not isinstance(suggestion.line, int) or suggestion.line < 0:
            logger.debug(
                f"Invalid line {suggestion.line!r} for {suggestion.file}, "
                f"skipping fix",
            )
            return False

        # line == 0 means "unspecified" — no line-targeted search possible.
        if suggestion.line >= 1:
            # Search outward from the target line (closest match wins).
            # Clamp to last line when the AI reports a stale/out-of-range
            # number so the search radius still gets a chance.
            target_idx = min(suggestion.line - 1, len(lines) - 1)  # 0-based
            search_order = [target_idx]
            for offset in range(1, search_radius + 1):
                if target_idx - offset >= 0:
                    search_order.append(target_idx - offset)
                if target_idx + offset < len(lines):
                    search_order.append(target_idx + offset)

        else:
            search_order = []

        for start in search_order:
            end = start + len(original_lines)
            if end > len(lines):
                continue

            window = lines[start:end]
            # Normalize trailing newline on last window line for comparison
            normalized_window = list(window)
            if normalized_window and not normalized_window[-1].endswith("\n"):
                normalized_window[-1] += "\n"

            if normalized_window == original_lines:
                suggested_lines = suggestion.suggested_code.splitlines(
                    keepends=True,
                )
                # Preserve trailing newline consistency
                if (
                    suggested_lines
                    and window
                    and window[-1].endswith("\n")
                    and not suggested_lines[-1].endswith("\n")
                ):
                    suggested_lines[-1] += "\n"

                new_lines = lines[:start] + suggested_lines + lines[end:]
                # Atomic write: write to temp file then rename
                fd, tmp = tempfile.mkstemp(
                    dir=path.parent,
                    suffix=".tmp",
                )
                try:
                    # os.fdopen transfers fd ownership to the file object.
                    # If os.fdopen itself raises, fd is still raw and must
                    # be closed manually to avoid a leak.
                    try:
                        fobj = os.fdopen(fd, "wb")
                    except BaseException:
                        os.close(fd)
                        raise
                    with fobj:
                        fobj.write("".join(new_lines).encode("utf-8"))
                    Path(tmp).replace(path)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise
                return True

        return False

    except OSError:
        return False


def apply_fixes(
    suggestions: Sequence[AIFixSuggestion],
    *,
    workspace_root: Path,
    auto_apply: bool = False,
    search_radius: int | None = None,
    undo_state: UndoState | None = None,
    capture_undo: bool = False,
    checkpoint_retention: int = DEFAULT_CHECKPOINT_RETENTION,
) -> list[AIFixSuggestion]:
    """Apply suggestions and return only those successfully applied.

    When ``capture_undo`` is True and ``undo_state`` is omitted, a git
    checkpoint (or file-snapshot fallback) is captured before the first
    mutation. Callers that already prepared an :class:`~lintro.ai.undo.UndoState`
    should pass it via ``undo_state`` and leave ``capture_undo`` False.

    Args:
        suggestions: Fix suggestions to apply.
        workspace_root: Root directory limiting writable paths.
        auto_apply: Reserved for future use; kept for API compatibility.
        search_radius: Max lines above/below the target line to search.
        undo_state: Optional pre-captured rollback state for this batch.
        capture_undo: When True, capture rollback state before mutating.
        checkpoint_retention: Max git checkpoint refs to retain when capturing.

    Returns:
        Suggestions that were applied successfully.
    """
    if capture_undo and undo_state is None and suggestions:
        undo_state = prepare_fix_batch(
            list(suggestions),
            workspace_root,
            retention=checkpoint_retention,
        )
        if undo_state is not None:
            logger.debug(
                "Captured fix-batch undo via {} backend ({} paths)",
                undo_state.kind,
                len(undo_state.paths),
            )

    extra: dict[str, int] = {}
    if search_radius is not None:
        extra["search_radius"] = search_radius
    return [
        fix
        for fix in suggestions
        if _apply_fix(
            fix,
            workspace_root=workspace_root,
            auto_apply=auto_apply,
            **extra,
        )
    ]


def rollback_applied_paths(
    undo_state: UndoState,
    suggestions: Sequence[AIFixSuggestion],
) -> None:
    """Restore files for ``suggestions`` from ``undo_state``.

    Used by interactive rejection and callers that need per-file rollback
    from the checkpoint tree (or file-snapshot fallback), not from
    in-memory suggestion ``original_code``.

    Args:
        undo_state: Handle from :func:`~lintro.ai.undo.prepare_fix_batch`.
        suggestions: Suggestions whose target files should be restored.
    """
    paths = [s.file for s in suggestions if s.file]
    if not paths:
        return
    restore_undo(undo_state, paths)
