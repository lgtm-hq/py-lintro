"""Tests for AI display renderers."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.display import (
    render_fixes,
    render_fixes_github,
    render_fixes_markdown,
    render_fixes_terminal,
)


class TestRenderFixesTerminal:
    """Tests for terminal fix rendering."""

    def test_empty(self):
        result = render_fixes_terminal([])
        assert_that(result).is_equal_to("")

    def test_renders_fix_header(self, sample_fix_suggestions):
        result = render_fixes_terminal(sample_fix_suggestions)
        assert_that(result).contains("1 fix suggestion")
        assert_that(result).contains("src/main.py:10")
        assert_that(result).contains("B101")

    def test_renders_tool_name_in_fix_header(self, sample_fix_suggestions):
        result = render_fixes_terminal(
            sample_fix_suggestions,
            tool_name="ruff",
        )
        assert_that(result).contains("ruff")
        assert_that(result).contains("1 fix suggestion")

    def test_renders_tool_name_in_code_panel(self, sample_fix_suggestions):
        result = render_fixes_terminal(sample_fix_suggestions)
        # tool_name="bandit" is set on the fixture
        assert_that(result).contains("bandit")

    def test_renders_explanation(self, sample_fix_suggestions):
        result = render_fixes_terminal(sample_fix_suggestions)
        assert_that(result).contains("Replace assert with if/raise")

    def test_renders_file_location(self, sample_fix_suggestions):
        result = render_fixes_terminal(sample_fix_suggestions)
        assert_that(result).contains("src/main.py:10")


class TestRenderFixesGitHub:
    """Tests for GitHub Actions fix rendering."""

    def test_uses_group_markers(self, sample_fix_suggestions):
        result = render_fixes_github(sample_fix_suggestions)
        assert_that(result).contains("::group::")
        assert_that(result).contains("::endgroup::")


class TestRenderFixesMarkdown:
    """Tests for Markdown fix rendering."""

    def test_uses_details_tags(self, sample_fix_suggestions):
        result = render_fixes_markdown(sample_fix_suggestions)
        assert_that(result).contains("<details>")
        assert_that(result).contains("```diff")


class TestRenderFixesAutoDetect:
    """Tests for environment-aware fix rendering."""

    def test_uses_github_in_actions(self, sample_fix_suggestions):
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
            result = render_fixes(sample_fix_suggestions)
            assert_that(result).contains("::group::")

    def test_uses_terminal_by_default(
        self,
        sample_fix_suggestions,
    ):
        with patch.dict("os.environ", {}, clear=True):
            result = render_fixes(sample_fix_suggestions)
            assert_that(result).contains("fix suggestion")

    def test_markdown_format_dispatches_to_markdown_renderer(
        self,
        sample_fix_suggestions,
    ):
        result = render_fixes(
            sample_fix_suggestions,
            output_format="markdown",
        )
        assert_that(result).contains("<details>")
        assert_that(result).contains("```diff")
        assert_that(result).does_not_contain("::group::")


class TestRenderSummaryAutoDetect:
    """Tests for environment-aware summary rendering."""

    def test_markdown_format_dispatches_to_markdown_renderer(self):
        from lintro.ai.display import render_summary
        from lintro.ai.models import AISummary

        summary = AISummary(overview="Test overview", key_patterns=["pattern1"])
        result = render_summary(summary, output_format="markdown")
        assert_that(result).contains("<details>")
        assert_that(result).contains("Test overview")
        assert_that(result).contains("pattern1")
