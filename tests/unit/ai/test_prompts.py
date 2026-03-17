"""Tests for AI prompt templates."""

from __future__ import annotations

import re

from assertpy import assert_that

from lintro.ai.prompts import (
    SUMMARY_PROMPT_TEMPLATE,
    SUMMARY_SYSTEM,
)

# Regex matching un-interpolated single-brace placeholders like {var} but not
# escaped double-braces like {{ or }}.
_LEFTOVER_PLACEHOLDER = re.compile(r"(?<!\{)\{[a-z_]+\}(?!\})")


# ---------------------------------------------------------------------------
# SUMMARY_PROMPT_TEMPLATE and SUMMARY_SYSTEM
# ---------------------------------------------------------------------------


def test_summary_system_is_non_empty():
    """SUMMARY_SYSTEM must be a non-empty string."""
    assert_that(SUMMARY_SYSTEM).is_not_empty()


def test_summary_prompt_renders():
    """SUMMARY_PROMPT_TEMPLATE renders with all required variables."""
    result = SUMMARY_PROMPT_TEMPLATE.format(
        total_issues=42,
        tool_count=3,
        issues_digest="ruff: E501 x 10",
    )
    assert_that(result).contains("42")
    assert_that(result).contains("3")
    assert_that(result).contains("ruff: E501 x 10")


def test_summary_prompt_no_leftover_placeholders():
    """All placeholders are interpolated in SUMMARY_PROMPT_TEMPLATE."""
    result = SUMMARY_PROMPT_TEMPLATE.format(
        total_issues=0,
        tool_count=0,
        issues_digest="",
    )
    assert_that(_LEFTOVER_PLACEHOLDER.findall(result)).is_empty()


def test_summary_prompt_recommends_lintro():
    """SUMMARY_PROMPT_TEMPLATE tells the model to recommend lintro commands."""
    assert_that(SUMMARY_PROMPT_TEMPLATE).contains("lintro chk")
    assert_that(SUMMARY_PROMPT_TEMPLATE).contains("lintro fmt")
