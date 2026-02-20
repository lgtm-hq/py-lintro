"""Tests for AI data models."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion


def test_models_fix_suggestion_defaults():
    fix = AIFixSuggestion()
    assert_that(fix.file).is_equal_to("")
    assert_that(fix.confidence).is_equal_to("medium")
    assert_that(fix.diff).is_equal_to("")
    assert_that(fix.tool_name).is_equal_to("")
    assert_that(fix.risk_level).is_equal_to("")
