"""Tests for AI display renderers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.display import (
    render_summary,
)
from lintro.ai.models import AISummary


# -- render_summary (auto-detect) ---------------------------------------------


def test_render_summary_auto_detect_markdown_format():
    """Verify render_summary uses Markdown format with details tags and content."""
    summary = AISummary(overview="Test overview", key_patterns=["pattern1"])
    result = render_summary(summary, output_format="markdown")
    assert_that(result).contains("<details>")
    assert_that(result).contains("Test overview")
    assert_that(result).contains("pattern1")
