"""Tests for AI data models."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion


class TestAIFixSuggestion:
    """Tests for AIFixSuggestion dataclass."""

    def test_defaults(self):
        fix = AIFixSuggestion()
        assert_that(fix.file).is_equal_to("")
        assert_that(fix.confidence).is_equal_to("medium")
        assert_that(fix.diff).is_equal_to("")
        assert_that(fix.tool_name).is_equal_to("")

    def test_with_values(self):
        fix = AIFixSuggestion(
            file="src/main.py",
            line=10,
            code="B101",
            tool_name="bandit",
            original_code="assert x",
            suggested_code="if not x: raise",
            diff="- assert x\n+ if not x: raise",
            explanation="Replace assert",
            confidence="high",
        )
        assert_that(fix.code).is_equal_to("B101")
        assert_that(fix.tool_name).is_equal_to("bandit")
        assert_that(fix.confidence).is_equal_to("high")
