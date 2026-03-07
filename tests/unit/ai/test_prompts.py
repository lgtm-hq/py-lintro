"""Tests for AI prompt templates."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.prompts import FIX_PROMPT_TEMPLATE, FIX_SYSTEM


def test_prompts_template_renders():
    """Verify the fix prompt template renders with all required placeholders."""
    assert_that(FIX_SYSTEM).is_not_empty()
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


def test_prompts_template_includes_risk_level():
    """Verify the fix prompt template contains a risk_level placeholder."""
    assert_that(FIX_PROMPT_TEMPLATE).contains("risk_level")
