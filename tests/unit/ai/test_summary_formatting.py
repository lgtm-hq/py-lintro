"""Tests for AI summary formatting and rendering.

Covers render_summary_terminal, render_summary_github,
and render_summary_markdown.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.display import (
    render_summary_github,
    render_summary_markdown,
    render_summary_terminal,
)
from lintro.ai.models import AISummary

# -- render_summary_terminal --------------------------------------------------


def test_render_summary_terminal_renders_overview():
    """Verify terminal rendering includes overview, patterns, actions, and effort."""
    summary = AISummary(
        overview="Code quality is good overall.",
        key_patterns=["Missing docstrings"],
        priority_actions=["Add docstrings to public functions"],
        estimated_effort="15 minutes",
    )
    output = render_summary_terminal(summary)
    assert_that(output).contains("Code quality is good overall")
    assert_that(output).contains("Missing docstrings")
    assert_that(output).contains("Add docstrings")
    assert_that(output).contains("15 minutes")


def test_render_summary_terminal_empty_summary():
    """Verify an empty AISummary produces empty terminal output."""
    summary = AISummary()
    output = render_summary_terminal(summary)
    assert_that(output).is_empty()


def test_render_summary_terminal_cost_display():
    """Verify token cost is shown or hidden based on the show_cost flag."""
    summary = AISummary(
        overview="Test",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.005,
    )
    with_cost = render_summary_terminal(summary, show_cost=True)
    assert_that(with_cost).contains("~150")

    without_cost = render_summary_terminal(summary, show_cost=False)
    assert_that(without_cost).does_not_contain("~150")


def test_render_summary_terminal_strips_leading_numbers_from_priority_actions():
    """Leading number prefixes are stripped from priority actions."""
    summary = AISummary(
        overview="Test",
        priority_actions=[
            "1. Fix OpenAI imports",
            "2) Replace asserts",
            "No number prefix here",
        ],
    )
    output = render_summary_terminal(summary)
    # Should not have double numbering like "1. 1. Fix"
    assert_that(output).does_not_contain("1. 1.")
    assert_that(output).does_not_contain("2. 2)")
    # Content should still appear
    assert_that(output).contains("Fix OpenAI imports")
    assert_that(output).contains("Replace asserts")
    assert_that(output).contains("No number prefix here")


def test_render_summary_terminal_renders_triage_suggestions():
    """Terminal rendering includes triage with stripped prefixes."""
    summary = AISummary(
        overview="Issues found",
        triage_suggestions=[
            "B101 in test files — assert is idiomatic, add # noqa: B101",
            "1. E501 in generated code — long lines expected",
        ],
    )
    output = render_summary_terminal(summary)
    assert_that(output).contains("Triage")
    assert_that(output).contains("B101 in test files")
    # Leading number should be stripped
    assert_that(output).contains("E501 in generated code")


def test_render_summary_terminal_omits_triage_when_empty():
    """Triage section is omitted when suggestions are empty."""
    summary = AISummary(overview="Clean code", triage_suggestions=[])
    output = render_summary_terminal(summary)
    assert_that(output).does_not_contain("Triage")


# -- render_summary_github ----------------------------------------------------


def test_render_summary_github_renders_with_groups():
    """Verify GitHub rendering wraps content in ::group::/::endgroup:: markers."""
    summary = AISummary(
        overview="Issues found",
        key_patterns=["Pattern A"],
        priority_actions=["Fix A"],
    )
    output = render_summary_github(summary)
    assert_that(output).contains("::group::")
    assert_that(output).contains("::endgroup::")
    assert_that(output).contains("Issues found")
    assert_that(output).contains("Pattern A")


def test_render_summary_github_strips_leading_numbers_from_priority_actions():
    """Verify GitHub rendering strips leading number prefixes from priority actions."""
    summary = AISummary(
        overview="Test",
        priority_actions=["1. Fix imports", "2. Add tests"],
    )
    output = render_summary_github(summary)
    assert_that(output).does_not_contain("1. 1.")
    assert_that(output).contains("Fix imports")


def test_render_summary_github_renders_triage_suggestions():
    """Verify GitHub rendering includes triage suggestions section."""
    summary = AISummary(
        overview="Issues found",
        triage_suggestions=[
            "B101 in test files — assert is idiomatic, add # noqa: B101",
            "E501 in generated code — long lines are expected",
        ],
    )
    output = render_summary_github(summary)
    assert_that(output).contains("Triage")
    assert_that(output).contains("B101 in test files")
    assert_that(output).contains("E501 in generated code")


def test_render_summary_github_omits_triage_when_empty():
    """Triage section omitted from GitHub output when empty."""
    summary = AISummary(overview="Clean code", triage_suggestions=[])
    output = render_summary_github(summary)
    assert_that(output).does_not_contain("Triage")


# -- render_summary_markdown ---------------------------------------------------


def test_render_summary_markdown_renders_with_details():
    """Verify markdown rendering wraps content in HTML details/summary tags."""
    summary = AISummary(
        overview="Some issues",
        key_patterns=["Pattern X"],
        priority_actions=["Action Y"],
        estimated_effort="1 hour",
    )
    output = render_summary_markdown(summary)
    assert_that(output).contains("<details>")
    assert_that(output).contains("</details>")
    assert_that(output).contains("Some issues")
    assert_that(output).contains("Pattern X")
    assert_that(output).contains("1 hour")


def test_render_summary_markdown_renders_triage_suggestions():
    """Verify markdown rendering includes triage suggestions section."""
    summary = AISummary(
        overview="Issues found",
        triage_suggestions=[
            "B101 in test files — assert is idiomatic, add # noqa: B101",
        ],
    )
    output = render_summary_markdown(summary)
    assert_that(output).contains("Triage")
    assert_that(output).contains("B101 in test files")
