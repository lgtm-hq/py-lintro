"""AI fix undo/rollback via patch files."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion

UNDO_DIR = ".lintro-cache/ai"
UNDO_FILE = "last_fixes.patch"


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
    patch_path.write_text("".join(patch_lines))
    return patch_path
