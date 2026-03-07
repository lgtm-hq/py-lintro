"""Tests for AI prompt templates."""

from __future__ import annotations

import re

import pytest
from assertpy import assert_that

from lintro.ai.prompts import (
    FIX_BATCH_PROMPT_TEMPLATE,
    FIX_PROMPT_TEMPLATE,
    FIX_SYSTEM,
    POST_FIX_SUMMARY_PROMPT_TEMPLATE,
    REFINEMENT_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
    SUMMARY_SYSTEM,
)

# Regex matching un-interpolated single-brace placeholders like {var} but not
# escaped double-braces like {{ or }}.
_LEFTOVER_PLACEHOLDER = re.compile(r"(?<!\{)\{[a-z_]+\}(?!\})")


# ---------------------------------------------------------------------------
# FIX_SYSTEM
# ---------------------------------------------------------------------------


def test_fix_system_is_non_empty():
    """FIX_SYSTEM must be a non-empty string."""
    assert_that(FIX_SYSTEM).is_not_empty()


def test_fix_system_mentions_json():
    """FIX_SYSTEM instructs the model to respond with JSON."""
    assert_that(FIX_SYSTEM).contains("JSON")


# ---------------------------------------------------------------------------
# FIX_PROMPT_TEMPLATE — basic rendering
# ---------------------------------------------------------------------------

_FIX_DEFAULTS = {
    "tool_name": "ruff",
    "code": "E501",
    "file": "main.py",
    "line": 42,
    "message": "Line too long",
    "context_start": 37,
    "context_end": 47,
    "code_context": "x = 1",
}


def test_prompts_template_renders():
    """Verify the fix prompt template renders with all required placeholders."""
    assert_that(FIX_SYSTEM).is_not_empty()
    result = FIX_PROMPT_TEMPLATE.format(**_FIX_DEFAULTS)
    assert_that(result).contains("ruff")
    assert_that(result).contains("main.py")


def test_prompts_template_includes_risk_level():
    """Verify the fix prompt template contains a risk_level placeholder."""
    assert_that(FIX_PROMPT_TEMPLATE).contains("risk_level")


def test_fix_prompt_no_leftover_placeholders():
    """All placeholders in FIX_PROMPT_TEMPLATE are interpolated."""
    result = FIX_PROMPT_TEMPLATE.format(**_FIX_DEFAULTS)
    assert_that(_LEFTOVER_PLACEHOLDER.findall(result)).is_empty()


def test_fix_prompt_contains_all_values():
    """Every supplied value appears verbatim in the rendered prompt."""
    result = FIX_PROMPT_TEMPLATE.format(**_FIX_DEFAULTS)
    for value in (
        "ruff",
        "E501",
        "main.py",
        "42",
        "Line too long",
        "37",
        "47",
        "x = 1",
    ):
        assert_that(result).contains(value)


# ---------------------------------------------------------------------------
# FIX_PROMPT_TEMPLATE — various issue types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,code,message",
    [
        ("ruff", "E501", "Line too long (120 > 79 characters)"),
        ("mypy", "attr-defined", "Module has no attribute 'foo'"),
        ("pylint", "C0114", "Missing module docstring"),
        ("flake8", "F401", "'os' imported but unused"),
        ("eslint", "no-unused-vars", "'x' is defined but never used"),
    ],
)
def test_fix_prompt_renders_various_issue_types(tool_name, code, message):
    """FIX_PROMPT_TEMPLATE renders correctly for diverse tool/code combos."""
    result = FIX_PROMPT_TEMPLATE.format(
        tool_name=tool_name,
        code=code,
        file="src/app.py",
        line=10,
        message=message,
        context_start=5,
        context_end=15,
        code_context="pass",
    )
    assert_that(result).contains(tool_name)
    assert_that(result).contains(code)
    assert_that(result).contains(message)
    assert_that(_LEFTOVER_PLACEHOLDER.findall(result)).is_empty()


# ---------------------------------------------------------------------------
# Special characters in messages and file paths
# ---------------------------------------------------------------------------


def test_fix_prompt_special_characters_in_message():
    """Quotes, newlines, and backslashes in the message survive rendering."""
    msg = "Expected \"int\" but got 'str'\nDetails: see C:\\path"
    result = FIX_PROMPT_TEMPLATE.format(
        **{**_FIX_DEFAULTS, "message": msg},
    )
    assert_that(result).contains('"int"')
    assert_that(result).contains("'str'")
    assert_that(result).contains("C:\\path")


def test_fix_prompt_unicode_in_file_path():
    """Unicode characters in file paths are preserved."""
    path = "src/modulos/\u00e9l\u00e8ve.py"
    result = FIX_PROMPT_TEMPLATE.format(
        **{**_FIX_DEFAULTS, "file": path},
    )
    assert_that(result).contains(path)


def test_fix_prompt_spaces_in_file_path():
    """File paths containing spaces are preserved."""
    path = "my project/src/hello world.py"
    result = FIX_PROMPT_TEMPLATE.format(
        **{**_FIX_DEFAULTS, "file": path},
    )
    assert_that(result).contains(path)


# ---------------------------------------------------------------------------
# Edge cases: empty code context, zero line, very long message
# ---------------------------------------------------------------------------


def test_fix_prompt_empty_code_context():
    """Empty code_context still produces a valid rendered string."""
    result = FIX_PROMPT_TEMPLATE.format(
        **{**_FIX_DEFAULTS, "code_context": ""},
    )
    assert_that(result).is_not_empty()
    assert_that(_LEFTOVER_PLACEHOLDER.findall(result)).is_empty()


def test_fix_prompt_zero_line_number():
    """Line number 0 renders without error."""
    result = FIX_PROMPT_TEMPLATE.format(
        **{**_FIX_DEFAULTS, "line": 0},
    )
    assert_that(result).contains("Line: 0")
    assert_that(_LEFTOVER_PLACEHOLDER.findall(result)).is_empty()


def test_fix_prompt_very_long_message():
    """A very long message does not break rendering."""
    long_msg = "A" * 10_000
    result = FIX_PROMPT_TEMPLATE.format(
        **{**_FIX_DEFAULTS, "message": long_msg},
    )
    assert_that(result).contains(long_msg)


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


# ---------------------------------------------------------------------------
# REFINEMENT_PROMPT_TEMPLATE
# ---------------------------------------------------------------------------


def test_refinement_prompt_renders():
    """REFINEMENT_PROMPT_TEMPLATE renders with all required variables."""
    result = REFINEMENT_PROMPT_TEMPLATE.format(
        tool_name="ruff",
        code="E501",
        file="main.py",
        line=10,
        previous_suggestion="old fix",
        new_error="still too long",
        context_start=5,
        context_end=15,
        code_context="x = 1",
    )
    assert_that(result).contains("old fix")
    assert_that(result).contains("still too long")
    assert_that(_LEFTOVER_PLACEHOLDER.findall(result)).is_empty()


# ---------------------------------------------------------------------------
# FIX_BATCH_PROMPT_TEMPLATE
# ---------------------------------------------------------------------------


def test_batch_prompt_renders():
    """FIX_BATCH_PROMPT_TEMPLATE renders with all required variables."""
    result = FIX_BATCH_PROMPT_TEMPLATE.format(
        tool_name="ruff",
        file="app.py",
        issues_list="1. E501 line 10\n2. E302 line 20",
        file_content="import os\n",
    )
    assert_that(result).contains("ruff")
    assert_that(result).contains("app.py")
    assert_that(result).contains("E501 line 10")
    assert_that(_LEFTOVER_PLACEHOLDER.findall(result)).is_empty()


# ---------------------------------------------------------------------------
# POST_FIX_SUMMARY_PROMPT_TEMPLATE
# ---------------------------------------------------------------------------


def test_post_fix_summary_prompt_renders():
    """POST_FIX_SUMMARY_PROMPT_TEMPLATE renders with all required variables."""
    result = POST_FIX_SUMMARY_PROMPT_TEMPLATE.format(
        applied=5,
        rejected=2,
        remaining=3,
        issues_digest="mypy: attr-defined x 3",
    )
    assert_that(result).contains("5")
    assert_that(result).contains("2")
    assert_that(result).contains("3")
    assert_that(result).contains("mypy: attr-defined x 3")


def test_post_fix_summary_prompt_no_leftover_placeholders():
    """All placeholders are interpolated in POST_FIX_SUMMARY_PROMPT_TEMPLATE."""
    result = POST_FIX_SUMMARY_PROMPT_TEMPLATE.format(
        applied=0,
        rejected=0,
        remaining=0,
        issues_digest="",
    )
    assert_that(_LEFTOVER_PLACEHOLDER.findall(result)).is_empty()


def test_post_fix_summary_recommends_lintro():
    """POST_FIX_SUMMARY_PROMPT_TEMPLATE tells the model to use lintro commands."""
    assert_that(POST_FIX_SUMMARY_PROMPT_TEMPLATE).contains("lintro chk")
    assert_that(POST_FIX_SUMMARY_PROMPT_TEMPLATE).contains("lintro fmt")
