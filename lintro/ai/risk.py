"""Risk classification and patch statistics for AI fix suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.ai.models import AIFixSuggestion

SAFE_STYLE_RISK = "safe-style"
BEHAVIORAL_RISK = "behavioral-risk"


@dataclass(frozen=True)
class PatchStats:
    """Compact patch statistics for one or more suggestions."""

    files: int = 0
    hunks: int = 0
    lines_added: int = 0
    lines_removed: int = 0


def classify_fix_risk(suggestion: AIFixSuggestion) -> str:
    """Classify a suggestion as safe style-only or behavioral risk.

    Uses the AI-reported ``risk_level`` from the fix response, combined
    with the suggestion's ``confidence``. Defaults to behavioral-risk
    when the risk_level is unknown or empty for safety.

    Args:
        suggestion: Fix suggestion to classify.

    Returns:
        Risk classification string: ``"safe-style"`` or ``"behavioral-risk"``.
    """
    risk = (suggestion.risk_level or "").strip().lower()

    if risk == SAFE_STYLE_RISK:
        # Trust AI classification for safe-style only when confidence
        # is high or medium — low-confidence safe claims default to risky.
        confidence = (suggestion.confidence or "").strip().lower()
        if confidence in ("high", "medium"):
            return SAFE_STYLE_RISK
        return BEHAVIORAL_RISK

    # Default: anything unknown or explicitly behavioral-risk
    return BEHAVIORAL_RISK


def is_safe_style_fix(suggestion: AIFixSuggestion) -> bool:
    """Return True when the suggestion is classified as safe style-only."""
    return classify_fix_risk(suggestion) == SAFE_STYLE_RISK


def calculate_patch_stats(suggestions: Sequence[AIFixSuggestion]) -> PatchStats:
    """Calculate patch stats for a group of fix suggestions."""
    if not suggestions:
        return PatchStats()

    files: set[str] = {str(Path(s.file)) for s in suggestions if s.file}
    hunks = 0
    lines_added = 0
    lines_removed = 0

    for suggestion in suggestions:
        diff = suggestion.diff or ""
        if diff.strip():
            for line in diff.splitlines():
                if line.startswith("@@"):
                    hunks += 1
                elif line.startswith("+") and not line.startswith("+++"):
                    lines_added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    lines_removed += 1
            continue

        # Fallback estimate when diff is unavailable.
        original_lines = suggestion.original_code.splitlines()
        suggested_lines = suggestion.suggested_code.splitlines()
        hunks += 1
        if len(suggested_lines) > len(original_lines):
            lines_added += len(suggested_lines) - len(original_lines)
        elif len(original_lines) > len(suggested_lines):
            lines_removed += len(original_lines) - len(suggested_lines)

    return PatchStats(
        files=len(files),
        hunks=hunks,
        lines_added=lines_added,
        lines_removed=lines_removed,
    )
