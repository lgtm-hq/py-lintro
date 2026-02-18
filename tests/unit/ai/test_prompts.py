"""Tests for AI prompt templates."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.prompts import (
    FIX_PROMPT_TEMPLATE,
    FIX_SYSTEM,
)


class TestFixPrompt:
    """Tests for fix prompt template."""

    def test_system_is_string(self):
        assert_that(FIX_SYSTEM).is_instance_of(str)
        assert_that(FIX_SYSTEM).is_not_empty()

    def test_template_has_placeholders(self):
        assert_that(FIX_PROMPT_TEMPLATE).contains("{tool_name}")
        assert_that(FIX_PROMPT_TEMPLATE).contains("{code}")
        assert_that(FIX_PROMPT_TEMPLATE).contains("{file}")
        assert_that(FIX_PROMPT_TEMPLATE).contains("{line}")
        assert_that(FIX_PROMPT_TEMPLATE).contains("{message}")
        assert_that(FIX_PROMPT_TEMPLATE).contains("{code_context}")

    def test_template_renders(self):
        result = FIX_PROMPT_TEMPLATE.format(
            tool_name="ruff",
            code="E501",
            file="main.py",
            line=42,
            message="Line too long",
            context_start=37,
            context_end=47,
            code_context="x = 1",
        )
        assert_that(result).contains("ruff")
        assert_that(result).contains("main.py")

    def test_template_includes_json_format(self):
        assert_that(FIX_PROMPT_TEMPLATE).contains("original_code")
        assert_that(FIX_PROMPT_TEMPLATE).contains("suggested_code")
        assert_that(FIX_PROMPT_TEMPLATE).contains("confidence")
