"""Tests for AI fix risk classification and patch statistics."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion
from lintro.ai.risk import (
    BEHAVIORAL_RISK,
    SAFE_STYLE_CODES_BY_TOOL,
    SAFE_STYLE_RISK,
    calculate_patch_stats,
    classify_fix_risk,
    is_safe_style_fix,
)


def test_classify_fix_risk_safe_style_code() -> None:
    """Test that safe style fix codes are classified with SAFE_STYLE_RISK."""
    suggestion = AIFixSuggestion(code="E501")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(SAFE_STYLE_RISK)
    assert_that(is_safe_style_fix(suggestion)).is_true()


def test_classify_fix_risk_behavioral_default() -> None:
    """Test that non-safe-style fix codes are classified with BEHAVIORAL_RISK."""
    suggestion = AIFixSuggestion(code="B101")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(BEHAVIORAL_RISK)
    assert_that(is_safe_style_fix(suggestion)).is_false()


def test_classify_fix_risk_tool_aware_safe() -> None:
    """Test tool-aware lookup classifies prettier FORMAT as safe."""
    suggestion = AIFixSuggestion(code="FORMAT", tool_name="prettier")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(SAFE_STYLE_RISK)


def test_classify_fix_risk_tool_aware_not_in_flat_set() -> None:
    """FORMAT is safe for prettier but not when tool_name is empty."""
    suggestion = AIFixSuggestion(code="FORMAT")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(BEHAVIORAL_RISK)


def test_classify_fix_risk_tool_aware_eslint() -> None:
    """Eslint SEMI is in the tool-aware set."""
    suggestion = AIFixSuggestion(code="SEMI", tool_name="eslint")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(SAFE_STYLE_RISK)


def test_safe_style_codes_by_tool_is_frozen() -> None:
    """Verify SAFE_STYLE_CODES_BY_TOOL is a frozenset of tuples."""
    assert_that(SAFE_STYLE_CODES_BY_TOOL).is_instance_of(frozenset)
    for item in SAFE_STYLE_CODES_BY_TOOL:
        assert_that(item).is_instance_of(tuple)
        assert_that(len(item)).is_equal_to(2)


def test_classify_fix_risk_falls_back_to_flat_set_with_unknown_tool() -> None:
    """E501 with unknown tool falls back to flat SAFE_STYLE_CODES set."""
    suggestion = AIFixSuggestion(code="E501", tool_name="black")
    assert_that(classify_fix_risk(suggestion)).is_equal_to(SAFE_STYLE_RISK)


def test_calculate_patch_stats_from_unified_diff() -> None:
    """Test calculating patch statistics from unified diff format."""
    """Test calculating patch statistics from a unified diff format."""
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
    """Test calculate_patch_stats fallback behavior when no diff is provided."""
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
