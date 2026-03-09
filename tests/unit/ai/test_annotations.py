"""Tests for GitHub Actions annotation rendering (#705)."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.display.fixes import (
    _escape_annotation,
    _risk_to_annotation_level,
    render_fixes_annotations,
)
from lintro.ai.display.summary import render_summary_annotations
from lintro.ai.models import AIFixSuggestion, AISummary

# -- _risk_to_annotation_level ------------------------------------------------


class TestRiskToAnnotationLevel:
    """Tests for risk level to annotation level mapping."""

    def test_high_maps_to_error(self) -> None:
        """Map high risk to error annotation level."""
        assert_that(_risk_to_annotation_level("high")).is_equal_to("error")

    def test_critical_maps_to_error(self) -> None:
        """Map critical risk to error annotation level."""
        assert_that(_risk_to_annotation_level("critical")).is_equal_to("error")

    def test_medium_maps_to_warning(self) -> None:
        """Map medium risk to warning annotation level."""
        assert_that(_risk_to_annotation_level("medium")).is_equal_to("warning")

    def test_behavioral_risk_maps_to_warning(self) -> None:
        """Map behavioral-risk to warning annotation level."""
        assert_that(_risk_to_annotation_level("behavioral-risk")).is_equal_to("warning")

    def test_low_maps_to_notice(self) -> None:
        """Map low risk to notice annotation level."""
        assert_that(_risk_to_annotation_level("low")).is_equal_to("notice")

    def test_safe_style_maps_to_notice(self) -> None:
        """Map safe-style risk to notice annotation level."""
        assert_that(_risk_to_annotation_level("safe-style")).is_equal_to("notice")

    def test_empty_defaults_to_warning(self) -> None:
        """Default empty risk to warning annotation level."""
        assert_that(_risk_to_annotation_level("")).is_equal_to("warning")

    def test_unknown_defaults_to_warning(self) -> None:
        """Default unknown risk to warning annotation level."""
        assert_that(_risk_to_annotation_level("something-else")).is_equal_to("warning")

    def test_case_insensitive(self) -> None:
        """Handle case-insensitive risk level input."""
        assert_that(_risk_to_annotation_level("HIGH")).is_equal_to("error")
        assert_that(_risk_to_annotation_level("Low")).is_equal_to("notice")

    def test_whitespace_stripped(self) -> None:
        """Strip surrounding whitespace from risk level."""
        assert_that(_risk_to_annotation_level("  high  ")).is_equal_to("error")


# -- _escape_annotation -------------------------------------------------------


class TestEscapeAnnotation:
    """Tests for annotation message escaping."""

    def test_escapes_percent(self) -> None:
        """Escape percent character for annotation."""
        assert_that(_escape_annotation("100%")).is_equal_to("100%25")

    def test_escapes_newline(self) -> None:
        """Escape newline character for annotation."""
        assert_that(_escape_annotation("line1\nline2")).is_equal_to("line1%0Aline2")

    def test_escapes_carriage_return(self) -> None:
        """Escape carriage return character for annotation."""
        assert_that(_escape_annotation("a\rb")).is_equal_to("a%0Db")

    def test_plain_text_unchanged(self) -> None:
        """Leave plain text without special characters unchanged."""
        assert_that(_escape_annotation("hello world")).is_equal_to("hello world")


# -- render_fixes_annotations -------------------------------------------------


class TestRenderFixesAnnotations:
    """Tests for GitHub Actions fix annotation rendering."""

    def test_empty_suggestions_returns_empty(self) -> None:
        """Return empty string for empty suggestion list."""
        result = render_fixes_annotations([])
        assert_that(result).is_equal_to("")

    def test_single_suggestion_emits_annotation(self) -> None:
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

    def test_high_risk_emits_error(self) -> None:
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

    def test_medium_risk_emits_warning(self) -> None:
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

    def test_no_risk_level_defaults_to_warning(self) -> None:
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

    def test_multiple_suggestions_emit_multiple_lines(self) -> None:
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

    def test_includes_confidence_in_message(self) -> None:
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

    def test_no_file_omits_file_prop(self) -> None:
        """Omit file property when suggestion has no file."""
        s = AIFixSuggestion(
            code="X",
            risk_level="low",
            explanation="No file",
        )
        result = render_fixes_annotations([s])
        assert_that(result).does_not_contain("file=")

    def test_code_without_tool_name(self) -> None:
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


# -- render_summary_annotations -----------------------------------------------


class TestRenderSummaryAnnotations:
    """Tests for GitHub Actions summary annotation rendering."""

    def test_empty_summary_returns_empty(self) -> None:
        """Return empty string for summary with empty overview."""
        summary = AISummary(overview="")
        result = render_summary_annotations(summary)
        assert_that(result).is_equal_to("")

    def test_key_patterns_emit_warnings(self) -> None:
        """Emit warning annotations for key patterns."""
        summary = AISummary(
            overview="Overview text",
            key_patterns=["Missing type hints", "No docstrings"],
        )
        result = render_summary_annotations(summary)
        assert_that(result).contains("::warning title=AI Pattern::Missing type hints")
        assert_that(result).contains("::warning title=AI Pattern::No docstrings")

    def test_priority_actions_emit_notices(self) -> None:
        """Emit notice annotations for priority actions."""
        summary = AISummary(
            overview="Overview text",
            priority_actions=["1. Fix imports", "2. Add tests"],
        )
        result = render_summary_annotations(summary)
        assert_that(result).contains("::notice title=AI Priority::Fix imports")
        assert_that(result).contains("::notice title=AI Priority::Add tests")

    def test_no_patterns_or_actions_returns_empty(self) -> None:
        """Return empty string when no patterns or actions present."""
        summary = AISummary(overview="Just an overview")
        result = render_summary_annotations(summary)
        assert_that(result).is_equal_to("")

    def test_escapes_special_characters(self) -> None:
        """Escape special characters in pattern annotations."""
        summary = AISummary(
            overview="Overview",
            key_patterns=["100% of files\nhave issues"],
        )
        result = render_summary_annotations(summary)
        assert_that(result).contains("%25")
        assert_that(result).contains("%0A")


# -- render_fixes auto-detection with annotations ----------------------------


class TestRenderFixesAutoDetectAnnotations:
    """Tests for auto-detection emitting annotations in GitHub Actions."""

    def test_github_actions_includes_annotations(
        self,
        sample_fix_suggestions: list[AIFixSuggestion],
    ) -> None:
        """Verify render_fixes emits annotations when in GitHub Actions."""
        from lintro.ai.display.fixes import render_fixes

        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
            result = render_fixes(sample_fix_suggestions)
            assert_that(result).contains("::group::")
            assert_that(result).contains("::warning")
            assert_that(result).contains("AI fix available")
