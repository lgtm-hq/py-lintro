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

# Conservative allowlist for deterministic style-only codes.
# Code-only set used as fallback when tool_name is empty.
SAFE_STYLE_CODES: frozenset[str] = frozenset(
    {
        "E501",
        "W291",
        "W292",
        "W293",
        "Q000",
        "Q001",
        "Q002",
    },
)

# Tool-aware allowlist keyed on (tool_name, code).
# Preferred over the flat set when tool_name is available.
SAFE_STYLE_CODES_BY_TOOL: frozenset[tuple[str, str]] = frozenset(
    {
        # ruff / flake8 style codes
        ("ruff", "E501"),
        ("ruff", "W291"),
        ("ruff", "W292"),
        ("ruff", "W293"),
        ("ruff", "Q000"),
        ("ruff", "Q001"),
        ("ruff", "Q002"),
        ("flake8", "E501"),
        ("flake8", "W291"),
        ("flake8", "W292"),
        ("flake8", "W293"),
        # prettier style codes
        ("prettier", "FORMAT"),
        ("prettier", "PRETTIER"),
        # eslint style codes
        ("eslint", "INDENT"),
        ("eslint", "SEMI"),
        ("eslint", "QUOTES"),
    },
)


@dataclass(frozen=True)
class PatchStats:
    """Compact patch statistics for one or more suggestions."""

    files: int = 0
    hunks: int = 0
    lines_added: int = 0
    lines_removed: int = 0


def classify_fix_risk(suggestion: AIFixSuggestion) -> str:
    """Classify a suggestion as safe style-only or behavioral risk.

    Prefers tool-aware lookup when ``tool_name`` is set, falls back
    to the code-only allowlist.
    """
    code = (suggestion.code or "").upper()
    tool = (suggestion.tool_name or "").lower()
    if tool and (tool, code) in SAFE_STYLE_CODES_BY_TOOL:
        return SAFE_STYLE_RISK
    if code in SAFE_STYLE_CODES:
        return SAFE_STYLE_RISK
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
