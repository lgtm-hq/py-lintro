"""Tests for AI fix risk classification and patch statistics."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion
from lintro.ai.risk import (
    BEHAVIORAL_RISK,
    SAFE_STYLE_RISK,
    PatchStats,
    calculate_patch_stats,
    classify_fix_risk,
    is_safe_style_fix,
)

# -- AI self-classification: risk_level + confidence combos ----------------


def test_safe_style_high_confidence_returns_safe_style() -> None:
    """risk_level='safe-style' with high confidence is classified safe-style."""
    suggestion = AIFixSuggestion(risk_level="safe-style", confidence="high")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(SAFE_STYLE_RISK)


def test_safe_style_medium_confidence_returns_safe_style() -> None:
    """risk_level='safe-style' with medium confidence is classified safe-style."""
    suggestion = AIFixSuggestion(risk_level="safe-style", confidence="medium")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(SAFE_STYLE_RISK)


def test_safe_style_low_confidence_returns_behavioral() -> None:
    """risk_level='safe-style' with low confidence demotes to behavioral-risk."""
    suggestion = AIFixSuggestion(risk_level="safe-style", confidence="low")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(BEHAVIORAL_RISK)


def test_safe_style_empty_confidence_returns_behavioral() -> None:
    """risk_level='safe-style' with empty confidence defaults to behavioral-risk."""
    suggestion = AIFixSuggestion(risk_level="safe-style", confidence="")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(BEHAVIORAL_RISK)


@pytest.mark.parametrize(
    ("risk_level", "confidence", "expected"),
    [
        ("safe-style", "high", SAFE_STYLE_RISK),
        ("safe-style", "medium", SAFE_STYLE_RISK),
        ("safe-style", "low", BEHAVIORAL_RISK),
        ("safe-style", "", BEHAVIORAL_RISK),
        ("behavioral-risk", "high", BEHAVIORAL_RISK),
        ("behavioral-risk", "medium", BEHAVIORAL_RISK),
        ("behavioral-risk", "low", BEHAVIORAL_RISK),
        ("", "high", BEHAVIORAL_RISK),
        ("", "", BEHAVIORAL_RISK),
        ("unknown", "high", BEHAVIORAL_RISK),
    ],
    ids=[
        "safe-high",
        "safe-medium",
        "safe-low",
        "safe-empty-conf",
        "behavioral-high",
        "behavioral-medium",
        "behavioral-low",
        "empty-risk-high",
        "empty-risk-empty-conf",
        "unknown-risk-high",
    ],
)
def test_classify_fix_risk_matrix(
    risk_level: str,
    confidence: str,
    expected: str,
) -> None:
    """Parametrized matrix covering all risk_level x confidence combinations."""
    suggestion = AIFixSuggestion(risk_level=risk_level, confidence=confidence)
    assert_that(classify_fix_risk(suggestion)).is_equal_to(expected)


# -- Edge cases for risk_level values --------------------------------------


def test_explicit_behavioral_risk_returns_behavioral() -> None:
    """risk_level='behavioral-risk' always returns behavioral-risk."""
    suggestion = AIFixSuggestion(risk_level="behavioral-risk", confidence="high")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(BEHAVIORAL_RISK)


def test_empty_risk_level_returns_behavioral() -> None:
    """Empty risk_level defaults to behavioral-risk regardless of confidence."""
    suggestion = AIFixSuggestion(risk_level="", confidence="high")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(BEHAVIORAL_RISK)


def test_unexpected_risk_level_returns_behavioral() -> None:
    """Unexpected/garbage risk_level defaults to behavioral-risk."""
    suggestion = AIFixSuggestion(risk_level="something-else", confidence="high")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(BEHAVIORAL_RISK)


def test_whitespace_risk_level_returns_behavioral() -> None:
    """Whitespace-only risk_level is treated as empty -> behavioral-risk."""
    suggestion = AIFixSuggestion(risk_level="  ", confidence="high")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(BEHAVIORAL_RISK)


def test_case_insensitive_risk_level() -> None:
    """risk_level matching is case-insensitive."""
    suggestion = AIFixSuggestion(risk_level="Safe-Style", confidence="high")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(SAFE_STYLE_RISK)


def test_case_insensitive_confidence() -> None:
    """Confidence matching is case-insensitive."""
    suggestion = AIFixSuggestion(risk_level="safe-style", confidence="HIGH")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(SAFE_STYLE_RISK)


# -- is_safe_style_fix delegation ------------------------------------------


def test_is_safe_style_fix_returns_true_for_safe_style() -> None:
    """is_safe_style_fix returns True when classify_fix_risk yields safe-style."""
    suggestion = AIFixSuggestion(risk_level="safe-style", confidence="high")
    assert_that(is_safe_style_fix(suggestion)).is_true()


def test_is_safe_style_fix_returns_false_for_behavioral() -> None:
    """is_safe_style_fix returns False when classify_fix_risk yields behavioral."""
    suggestion = AIFixSuggestion(risk_level="behavioral-risk", confidence="high")
    assert_that(is_safe_style_fix(suggestion)).is_false()


def test_is_safe_style_fix_returns_false_for_empty_risk() -> None:
    """is_safe_style_fix returns False for empty risk_level."""
    suggestion = AIFixSuggestion(risk_level="", confidence="medium")
    assert_that(is_safe_style_fix(suggestion)).is_false()


def test_is_safe_style_fix_returns_false_for_low_confidence_safe() -> None:
    """is_safe_style_fix returns False when safe-style has low confidence."""
    suggestion = AIFixSuggestion(risk_level="safe-style", confidence="low")
    assert_that(is_safe_style_fix(suggestion)).is_false()


# -- PatchStats dataclass --------------------------------------------------


def test_patch_stats_defaults() -> None:
    """PatchStats defaults to all zeros."""
    stats = PatchStats()
    assert_that(stats.files).is_equal_to(0)
    assert_that(stats.hunks).is_equal_to(0)
    assert_that(stats.lines_added).is_equal_to(0)
    assert_that(stats.lines_removed).is_equal_to(0)


def test_patch_stats_is_frozen() -> None:
    """PatchStats is a frozen dataclass."""
    stats = PatchStats(files=1, hunks=2, lines_added=3, lines_removed=4)
    with pytest.raises(AttributeError):
        stats.files = 99  # type: ignore[misc]  # intentionally mutating frozen dataclass


# -- calculate_patch_stats -------------------------------------------------


def test_calculate_patch_stats_empty_list() -> None:
    """Empty suggestions list produces zero stats."""
    stats = calculate_patch_stats([])
    assert_that(stats).is_equal_to(PatchStats())


def test_calculate_patch_stats_from_unified_diff() -> None:
    """Patch stats are calculated correctly from a unified diff."""
    suggestion = AIFixSuggestion(
        file="src/main.py",
        diff=(
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,2 +1,3 @@\n"
            "-a = 1\n"
            "+a = 2\n"
            "+b = 3\n"
        ),
    )

    stats = calculate_patch_stats([suggestion])
    assert_that(stats.files).is_equal_to(1)
    assert_that(stats.hunks).is_equal_to(1)
    assert_that(stats.lines_added).is_equal_to(2)
    assert_that(stats.lines_removed).is_equal_to(1)


def test_calculate_patch_stats_fallback_without_diff() -> None:
    """Fallback estimate is used when no diff is provided."""
    suggestion = AIFixSuggestion(
        file="src/main.py",
        original_code="a = 1\n",
        suggested_code="a = 1\nb = 2\n",
    )

    stats = calculate_patch_stats([suggestion])
    assert_that(stats.files).is_equal_to(1)
    assert_that(stats.hunks).is_equal_to(1)
    assert_that(stats.lines_added).is_equal_to(1)
    assert_that(stats.lines_removed).is_equal_to(0)


def test_calculate_patch_stats_multiple_files() -> None:
    """Multiple suggestions across files are aggregated correctly."""
    suggestions = [
        AIFixSuggestion(
            file="a.py",
            diff=("--- a/a.py\n" "+++ b/a.py\n" "@@ -1 +1 @@\n" "-old\n" "+new\n"),
        ),
        AIFixSuggestion(
            file="b.py",
            diff=("--- a/b.py\n" "+++ b/b.py\n" "@@ -1 +1,2 @@\n" "+added\n"),
        ),
    ]

    stats = calculate_patch_stats(suggestions)
    assert_that(stats.files).is_equal_to(2)
    assert_that(stats.hunks).is_equal_to(2)
    assert_that(stats.lines_added).is_equal_to(2)
    assert_that(stats.lines_removed).is_equal_to(1)


def test_calculate_patch_stats_fallback_lines_removed() -> None:
    """Fallback correctly counts lines removed when suggested code is shorter."""
    suggestion = AIFixSuggestion(
        file="c.py",
        original_code="line1\nline2\nline3\n",
        suggested_code="line1\n",
    )

    stats = calculate_patch_stats([suggestion])
    assert_that(stats.lines_removed).is_equal_to(2)
    assert_that(stats.lines_added).is_equal_to(0)
