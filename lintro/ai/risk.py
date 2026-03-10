"""Risk classification and patch statistics for AI fix suggestions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lintro.ai.enums import ConfidenceLevel, RiskLevel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.ai.models import AIFixSuggestion

SAFE_STYLE_RISK = RiskLevel.SAFE_STYLE
BEHAVIORAL_RISK = RiskLevel.BEHAVIORAL_RISK


@dataclass(frozen=True)
class PatchStats:
    """Compact patch statistics for one or more suggestions."""

    files: int = 0
    hunks: int = 0
    lines_added: int = 0
    lines_removed: int = 0


def _ast_equivalent(original: str, suggested: str) -> bool | None:
    """Compare ASTs of original and suggested Python code.

    Returns True if both snippets parse to the same AST (style-only change),
    False if they differ (behavioral change), or None if either snippet
    is not valid Python (fall back to heuristic).
    """
    import ast

    try:
        orig_ast = ast.dump(ast.parse(original))
        sugg_ast = ast.dump(ast.parse(suggested))
        return orig_ast == sugg_ast
    except SyntaxError:
        return None  # Not parseable, fall back to heuristic


def _diff_is_style_only(suggestion: AIFixSuggestion) -> bool:
    """Check whether the diff only changes whitespace/style.

    First attempts an AST comparison for Python code. If both snippets
    parse successfully, the AST result is authoritative. Otherwise falls
    back to comparing original and suggested code after stripping
    whitespace and normalizing quotes.
    """
    original = suggestion.original_code or ""
    suggested = suggestion.suggested_code or ""

    # Try AST comparison first (authoritative for valid Python)
    ast_result = _ast_equivalent(original, suggested)
    if ast_result is not None:
        return ast_result

    def _normalize(text: str) -> str:
        """Normalize for style-only comparison without altering semantics.

        Only performs safe normalizations: trim edges, normalize line
        endings and consecutive blank lines, and remove trailing commas
        before closing brackets. Does NOT remove internal whitespace or
        rewrite quote characters, which could mask behavioral changes.
        """
        # Trim leading/trailing whitespace
        text = text.strip()
        # Normalize line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Collapse consecutive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Remove trailing commas before closing brackets
        text = re.sub(r",(\s*[}\])])", r"\1", text)
        return text

    # Fall back to whitespace/quote normalization heuristic
    return _normalize(original) == _normalize(suggested)


def classify_fix_risk(suggestion: AIFixSuggestion) -> str:
    """Classify a suggestion as safe style-only or behavioral risk.

    Uses the AI-reported ``risk_level`` from the fix response, combined
    with the suggestion's ``confidence``. Applies a heuristic cross-check:
    if the diff changes non-whitespace content beyond quotes and trailing
    commas, the fix is downgraded to behavioral-risk regardless of AI claim.

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
        if confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM):
            # Heuristic cross-check: downgrade if the diff changes
            # non-whitespace/non-quote content.
            if not _diff_is_style_only(suggestion):
                return BEHAVIORAL_RISK
            return SAFE_STYLE_RISK
        return BEHAVIORAL_RISK

    # Default: anything unknown or explicitly behavioral-risk
    return BEHAVIORAL_RISK


def is_safe_style_fix(suggestion: AIFixSuggestion) -> bool:
    """Return True when the suggestion is classified as safe style-only."""
    return classify_fix_risk(suggestion) == SAFE_STYLE_RISK


def calculate_patch_stats(suggestions: Sequence[AIFixSuggestion]) -> PatchStats:
    """Calculate patch stats for a group of fix suggestions."""
    import difflib

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

        # Fallback estimate when diff is unavailable: compute actual churn.
        original_lines = suggestion.original_code.splitlines()
        suggested_lines = suggestion.suggested_code.splitlines()
        matcher = difflib.SequenceMatcher(
            None,
            original_lines,
            suggested_lines,
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "replace":
                lines_removed += i2 - i1
                lines_added += j2 - j1
                hunks += 1
            elif tag == "delete":
                lines_removed += i2 - i1
                hunks += 1
            elif tag == "insert":
                lines_added += j2 - j1
                hunks += 1

    return PatchStats(
        files=len(files),
        hunks=hunks,
        lines_added=lines_added,
        lines_removed=lines_removed,
    )
