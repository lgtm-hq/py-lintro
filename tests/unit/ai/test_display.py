"""Tests for AI display renderers."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.display import (
    render_fixes,
    render_fixes_github,
    render_fixes_markdown,
    render_fixes_terminal,
    render_summary,
)
from lintro.ai.models import AISummary

# -- render_fixes_terminal ---------------------------------------------------


def test_render_fixes_terminal_empty():
    """Verify terminal renderer returns empty string for no fix suggestions."""
    result = render_fixes_terminal([])
    assert_that(result).is_equal_to("")


def test_render_fixes_terminal_renders_fix_header(sample_fix_suggestions):
    """Verify terminal renderer includes fix count, location, and rule code."""
    result = render_fixes_terminal(sample_fix_suggestions)
    assert_that(result).contains("1 fix suggestion")
    assert_that(result).contains("src/main.py:10")
    assert_that(result).contains("B101")


def test_render_fixes_terminal_renders_tool_name_in_fix_header(sample_fix_suggestions):
    """Verify terminal renderer includes the tool name in the fix header."""
    result = render_fixes_terminal(
        sample_fix_suggestions,
        tool_name="ruff",
    )
    assert_that(result).contains("ruff")
    assert_that(result).contains("1 fix suggestion")


def test_render_fixes_terminal_renders_tool_name_in_code_panel(sample_fix_suggestions):
    """Verify terminal renderer includes the fixture tool name in the code panel."""
    result = render_fixes_terminal(sample_fix_suggestions)
    # tool_name="bandit" is set on the fixture
    assert_that(result).contains("bandit")


def test_render_fixes_terminal_renders_explanation(sample_fix_suggestions):
    """Verify terminal renderer includes the fix explanation text."""
    result = render_fixes_terminal(sample_fix_suggestions)
    assert_that(result).contains("Replace assert with if/raise")


def test_render_fixes_terminal_renders_file_location(sample_fix_suggestions):
    """Verify terminal renderer includes the file path and line number."""
    result = render_fixes_terminal(sample_fix_suggestions)
    assert_that(result).contains("src/main.py:10")


# -- render_fixes_github ------------------------------------------------------


def test_render_fixes_github_uses_group_markers(sample_fix_suggestions):
    """Verify GitHub renderer wraps output in ::group:: and ::endgroup:: markers."""
    result = render_fixes_github(sample_fix_suggestions)
    assert_that(result).contains("::group::")
    assert_that(result).contains("::endgroup::")


# -- render_fixes_markdown ----------------------------------------------------


def test_render_fixes_markdown_uses_details_tags(sample_fix_suggestions):
    """Verify Markdown renderer uses HTML details tags and diff code blocks."""
    result = render_fixes_markdown(sample_fix_suggestions)
    assert_that(result).contains("<details>")
    assert_that(result).contains("```diff")


# -- render_fixes (auto-detect) -----------------------------------------------


def test_render_fixes_auto_detect_uses_github_in_actions(sample_fix_suggestions):
    """Verify render_fixes auto-detects GitHub Actions and uses the GitHub renderer."""
    with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
        result = render_fixes(sample_fix_suggestions)
        assert_that(result).contains("::group::")


def test_render_fixes_auto_detect_uses_terminal_by_default(sample_fix_suggestions):
    """Verify render_fixes defaults to terminal renderer outside CI environments."""
    with patch.dict("os.environ", {}, clear=True):
        result = render_fixes(sample_fix_suggestions)
        assert_that(result).contains("fix suggestion")


def test_render_fixes_auto_detect_markdown_format(sample_fix_suggestions):
    """Verify render_fixes uses Markdown renderer when output_format is 'markdown'."""
    result = render_fixes(
        sample_fix_suggestions,
        output_format="markdown",
    )
    assert_that(result).contains("<details>")
    assert_that(result).contains("```diff")
    assert_that(result).does_not_contain("::group::")


# -- render_summary (auto-detect) ---------------------------------------------


def test_render_summary_auto_detect_markdown_format():
    """Verify render_summary uses Markdown format with details tags and content."""
    summary = AISummary(overview="Test overview", key_patterns=["pattern1"])
    result = render_summary(summary, output_format="markdown")
    assert_that(result).contains("<details>")
    assert_that(result).contains("Test overview")
    assert_that(result).contains("pattern1")
