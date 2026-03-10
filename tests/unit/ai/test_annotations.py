"""Tests for GitHub Actions annotation rendering (#705)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.display.fixes import (
    _escape_annotation,
    _risk_to_annotation_level,
    render_fixes_annotations,
)
from lintro.ai.display.summary import render_summary_annotations
from lintro.ai.models import AIFixSuggestion, AISummary

# -- TestRiskToAnnotationLevel: Tests for risk level to annotation level mapping.


@pytest.mark.parametrize(
    "input_val, expected",
    [
        ("high", "error"),
        ("critical", "error"),
        ("medium", "warning"),
        ("behavioral-risk", "warning"),
        ("low", "notice"),
        ("safe-style", "notice"),
        ("", "warning"),
        ("something-else", "warning"),
        ("HIGH", "error"),
        ("Low", "notice"),
        ("  high  ", "error"),
    ],
)
def test_risk_to_annotation_level(input_val: str, expected: str) -> None:
    """Map risk level to annotation severity."""
    assert_that(_risk_to_annotation_level(input_val)).is_equal_to(expected)


# -- TestEscapeAnnotation: Tests for annotation message escaping. ------------


def test_escapes_percent() -> None:
    """Escape percent character for annotation."""
    assert_that(_escape_annotation("100%")).is_equal_to("100%25")


def test_escapes_newline() -> None:
    """Escape newline character for annotation."""
    assert_that(_escape_annotation("line1\nline2")).is_equal_to("line1%0Aline2")


def test_escapes_carriage_return() -> None:
    """Escape carriage return character for annotation."""
    assert_that(_escape_annotation("a\rb")).is_equal_to("a%0Db")


def test_plain_text_unchanged() -> None:
    """Leave plain text without special characters unchanged."""
    assert_that(_escape_annotation("hello world")).is_equal_to("hello world")


# -- TestRenderFixesAnnotations: Tests for GitHub Actions fix annotation rendering.


def test_empty_suggestions_returns_empty() -> None:
    """Return empty string for empty suggestion list."""
    result = render_fixes_annotations([])
    assert_that(result).is_equal_to("")


def test_single_suggestion_emits_annotation() -> None:
    """Emit annotation with file, line, title, and message."""
    s = AIFixSuggestion(
        file="src/main.py",
        line=10,
        code="B101",
        tool_name="bandit",
        explanation="Replace assert",
        confidence="high",
        risk_level="low",
    )
    result = render_fixes_annotations([s])
    assert_that(result).contains("::notice")
    assert_that(result).contains("file=src/main.py")
    assert_that(result).contains("line=10")
    assert_that(result).contains("title=bandit(B101)")
    assert_that(result).contains("AI fix available [B101]: Replace assert")


def test_high_risk_emits_error() -> None:
    """Emit error-level annotation for high risk suggestion."""
    s = AIFixSuggestion(
        file="src/main.py",
        line=5,
        code="S101",
        risk_level="high",
        explanation="Dangerous pattern",
    )
    result = render_fixes_annotations([s])
    assert_that(result).starts_with("::error")


def test_medium_risk_emits_warning() -> None:
    """Emit warning-level annotation for medium risk suggestion."""
    s = AIFixSuggestion(
        file="src/main.py",
        line=5,
        code="W001",
        risk_level="medium",
        explanation="Some warning",
    )
    result = render_fixes_annotations([s])
    assert_that(result).starts_with("::warning")


def test_no_risk_level_defaults_to_warning() -> None:
    """Default to warning-level annotation when risk level is empty."""
    s = AIFixSuggestion(
        file="src/main.py",
        line=5,
        code="X001",
        risk_level="",
        explanation="Some issue",
    )
    result = render_fixes_annotations([s])
    assert_that(result).starts_with("::warning")


def test_multiple_suggestions_emit_multiple_lines() -> None:
    """Emit one annotation line per suggestion."""
    suggestions = [
        AIFixSuggestion(
            file="a.py",
            line=1,
            code="A",
            risk_level="low",
            explanation="Fix A",
        ),
        AIFixSuggestion(
            file="b.py",
            line=2,
            code="B",
            risk_level="high",
            explanation="Fix B",
        ),
    ]
    result = render_fixes_annotations(suggestions)
    lines = result.strip().split("\n")
    assert_that(lines).is_length(2)
    assert_that(lines[0]).contains("::notice")
    assert_that(lines[1]).contains("::error")


def test_includes_confidence_in_message() -> None:
    """Include confidence level in annotation message."""
    s = AIFixSuggestion(
        file="x.py",
        line=1,
        code="C",
        confidence="high",
        risk_level="low",
        explanation="Fix it",
    )
    result = render_fixes_annotations([s])
    assert_that(result).contains("(confidence: high)")


def test_no_file_omits_file_prop() -> None:
    """Omit file property when suggestion has no file."""
    s = AIFixSuggestion(
        code="X",
        risk_level="low",
        explanation="No file",
    )
    result = render_fixes_annotations([s])
    assert_that(result).does_not_contain("file=")


def test_code_without_tool_name() -> None:
    """Use bare code as title when tool name is absent."""
    s = AIFixSuggestion(
        file="f.py",
        line=1,
        code="E501",
        risk_level="low",
        explanation="Line too long",
    )
    result = render_fixes_annotations([s])
    assert_that(result).contains("title=E501")


# -- TestRenderSummaryAnnotations: summary annotation rendering. -


def test_empty_summary_returns_empty() -> None:
    """Return empty string for summary with empty overview."""
    summary = AISummary(overview="")
    result = render_summary_annotations(summary)
    assert_that(result).is_equal_to("")


def test_key_patterns_emit_warnings() -> None:
    """Emit warning annotations for key patterns."""
    summary = AISummary(
        overview="Overview text",
        key_patterns=["Missing type hints", "No docstrings"],
    )
    result = render_summary_annotations(summary)
    assert_that(result).contains("::warning title=AI Pattern::Missing type hints")
    assert_that(result).contains("::warning title=AI Pattern::No docstrings")


def test_priority_actions_emit_notices() -> None:
    """Emit notice annotations for priority actions."""
    summary = AISummary(
        overview="Overview text",
        priority_actions=["1. Fix imports", "2. Add tests"],
    )
    result = render_summary_annotations(summary)
    assert_that(result).contains("::notice title=AI Priority::Fix imports")
    assert_that(result).contains("::notice title=AI Priority::Add tests")


def test_no_patterns_or_actions_returns_empty() -> None:
    """Return empty string when no patterns or actions present."""
    summary = AISummary(overview="Just an overview")
    result = render_summary_annotations(summary)
    assert_that(result).is_equal_to("")


def test_escapes_special_characters() -> None:
    """Escape special characters in pattern annotations."""
    summary = AISummary(
        overview="Overview",
        key_patterns=["100% of files\nhave issues"],
    )
    result = render_summary_annotations(summary)
    assert_that(result).contains("%25")
    assert_that(result).contains("%0A")


# -- TestRenderFixesAutoDetectAnnotations: auto-detect annotations.


def test_github_actions_includes_annotations(
    sample_fix_suggestions: list[AIFixSuggestion],
) -> None:
    """Verify render_fixes emits annotations when in GitHub Actions."""
    from lintro.ai.display.fixes import render_fixes

    with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
        result = render_fixes(sample_fix_suggestions)
        assert_that(result).contains("::group::")
        assert_that(result).contains("::warning")
        assert_that(result).contains("AI fix available")
