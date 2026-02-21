"""Apply AI-generated fix suggestions to source files.

Handles line-targeted replacement with search-radius fallback.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from loguru import logger

from lintro.ai.models import AIFixSuggestion
from lintro.ai.paths import resolve_workspace_file


def _apply_fix(
    suggestion: AIFixSuggestion,
    *,
    workspace_root: Path | None = None,
    auto_apply: bool = False,
    search_radius: int = 5,
) -> bool:
    """Apply a single fix suggestion to the file.

    Uses line-number-targeted replacement to avoid matching the wrong
    occurrence when the same code pattern appears elsewhere in the file.
    Falls back to first-occurrence replacement if line targeting fails,
    unless ``auto_apply`` is True (only allows line-targeted).

    Args:
        suggestion: Fix suggestion to apply.
        workspace_root: Optional root directory limiting writable paths.
        auto_apply: When True, skip the fallback first-occurrence
            replacement (only allow line-targeted). Used by auto-apply
            paths in the pipeline for safety.
        search_radius: Max lines above/below the target line to search
            for the original code pattern.

    Returns:
        True if the fix was applied successfully.
    """
    try:
        path = Path(suggestion.file)
        if workspace_root is not None:
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

        # Search outward from the target line (closest match wins)
        target_idx = max(0, suggestion.line - 1)  # 0-based
        search_order = [target_idx]
        for offset in range(1, search_radius + 1):
            if target_idx - offset >= 0:
                search_order.append(target_idx - offset)
            if target_idx + offset < len(lines):
                search_order.append(target_idx + offset)

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
                path.write_text("".join(new_lines), encoding="utf-8")
                return True

        # Fallback: first-occurrence string replacement.
        # Warning: this may apply the fix to the wrong location if
        # the original code appears earlier in the file.
        # Skipped when auto_apply is True for safety.
        if not auto_apply and suggestion.original_code in content:
            logger.debug(
                f"Line-targeted replacement failed for "
                f"{suggestion.file}:{suggestion.line}, "
                f"falling back to first-occurrence string replacement",
            )
            new_content = content.replace(
                suggestion.original_code,
                suggestion.suggested_code,
                1,
            )
            path.write_text(new_content, encoding="utf-8")
            return True

        return False

    except OSError:
        return False


def apply_fixes(
    suggestions: Sequence[AIFixSuggestion],
    *,
    workspace_root: Path | None = None,
    auto_apply: bool = False,
) -> list[AIFixSuggestion]:
    """Apply suggestions and return only those successfully applied."""
    return [
        fix
        for fix in suggestions
        if _apply_fix(fix, workspace_root=workspace_root, auto_apply=auto_apply)
    ]
