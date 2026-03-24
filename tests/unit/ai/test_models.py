"""Tests for AI data models."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion


def test_models_fix_suggestion_defaults():
    """All AIFixSuggestion fields have expected defaults."""
    fix = AIFixSuggestion()
    assert_that(fix.file).is_equal_to("")
    assert_that(fix.line).is_equal_to(0)
    assert_that(fix.code).is_equal_to("")
    assert_that(fix.tool_name).is_equal_to("")
    assert_that(fix.original_code).is_equal_to("")
    assert_that(fix.suggested_code).is_equal_to("")
    assert_that(fix.diff).is_equal_to("")
    assert_that(fix.explanation).is_equal_to("")
    assert_that(fix.confidence).is_equal_to("medium")
    assert_that(fix.risk_level).is_equal_to("")
    assert_that(fix.input_tokens).is_equal_to(0)
    assert_that(fix.output_tokens).is_equal_to(0)
    assert_that(fix.cost_estimate).is_equal_to(0.0)
