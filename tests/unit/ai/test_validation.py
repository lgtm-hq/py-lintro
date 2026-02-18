"""Tests for AI fix validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.display import render_validation_terminal
from lintro.ai.models import AIFixSuggestion
from lintro.ai.validation import ValidationResult, validate_applied_fixes
from lintro.models.core.tool_result import ToolResult


def _make_suggestion(
    *,
    file: str = "src/main.py",
    line: int = 10,
    code: str = "B101",
    tool_name: str = "ruff",
) -> AIFixSuggestion:
    return AIFixSuggestion(
        file=file,
        line=line,
        code=code,
        tool_name=tool_name,
        original_code="assert x",
        suggested_code="if not x: raise",
        explanation="Replace assert",
    )


class TestValidateAppliedFixes:
    """Tests for validate_applied_fixes."""

    def test_returns_none_for_empty(self):
        result = validate_applied_fixes([])
        assert_that(result).is_none()

    @patch("lintro.ai.validation._run_tool_check")
    def test_verified_when_issue_gone(self, mock_check):
        mock_check.return_value = []  # No issues remain
        suggestion = _make_suggestion()

        result = validate_applied_fixes([suggestion])

        assert_that(result).is_not_none()
        assert_that(result.verified).is_equal_to(1)
        assert_that(result.unverified).is_equal_to(0)

    @patch("lintro.ai.validation._run_tool_check")
    def test_unverified_when_issue_remains(self, mock_check):
        remaining = MagicMock()
        remaining.file = "src/main.py"
        remaining.code = "B101"
        mock_check.return_value = [remaining]

        suggestion = _make_suggestion()
        result = validate_applied_fixes([suggestion])

        assert_that(result).is_not_none()
        assert_that(result.verified).is_equal_to(0)
        assert_that(result.unverified).is_equal_to(1)
        assert_that(result.details).is_length(1)

    @patch("lintro.ai.validation._run_tool_check")
    def test_mixed_verified_and_unverified(self, mock_check):
        remaining = MagicMock()
        remaining.file = "src/main.py"
        remaining.code = "B101"
        mock_check.return_value = [remaining]

        s1 = _make_suggestion(code="B101")
        s2 = _make_suggestion(code="E501")  # This one is resolved

        result = validate_applied_fixes([s1, s2])

        assert_that(result.verified).is_equal_to(1)
        assert_that(result.unverified).is_equal_to(1)

    @patch("lintro.ai.validation._run_tool_check")
    def test_skips_unknown_tool(self, mock_check):
        suggestion = _make_suggestion(tool_name="unknown")
        result = validate_applied_fixes([suggestion])

        assert_that(result).is_not_none()
        assert_that(result.verified).is_equal_to(0)
        assert_that(result.unverified).is_equal_to(0)
        mock_check.assert_not_called()

    @patch("lintro.ai.validation._run_tool_check")
    def test_skips_when_check_returns_none(self, mock_check):
        mock_check.return_value = None  # Tool not available
        suggestion = _make_suggestion()

        result = validate_applied_fixes([suggestion])

        assert_that(result.verified).is_equal_to(0)
        assert_that(result.unverified).is_equal_to(0)

    @patch("lintro.ai.validation._run_tool_check")
    def test_groups_by_tool(self, mock_check):
        mock_check.return_value = []

        s1 = _make_suggestion(tool_name="ruff")
        s2 = _make_suggestion(tool_name="mypy", code="error")

        result = validate_applied_fixes([s1, s2])

        assert_that(result.verified).is_equal_to(2)
        assert_that(mock_check.call_count).is_equal_to(2)


class TestRunToolCheck:
    """Tests for _run_tool_check."""

    @patch("lintro.tools.tool_manager.get_tool")
    def test_returns_issues(self, mock_get_tool):
        from lintro.ai.validation import _run_tool_check

        mock_issue = MagicMock()
        mock_result = ToolResult(
            name="ruff",
            success=True,
            issues_count=1,
            issues=[mock_issue],
        )
        mock_tool = MagicMock()
        mock_tool.check.return_value = mock_result
        mock_get_tool.return_value = mock_tool

        issues = _run_tool_check("ruff", ["src/main.py"])
        assert_that(issues).is_length(1)

    @patch("lintro.tools.tool_manager.get_tool")
    def test_returns_none_on_error(self, mock_get_tool):
        from lintro.ai.validation import _run_tool_check

        mock_tool = MagicMock()
        mock_tool.check.side_effect = RuntimeError("fail")
        mock_get_tool.return_value = mock_tool

        issues = _run_tool_check("ruff", ["src/main.py"])
        assert_that(issues).is_none()

    @patch("lintro.tools.tool_manager.get_tool")
    def test_returns_none_for_missing_tool(self, mock_get_tool):
        from lintro.ai.validation import _run_tool_check

        mock_get_tool.side_effect = KeyError("no such tool")

        issues = _run_tool_check("nonexistent", ["src/main.py"])
        assert_that(issues).is_none()


class TestRenderValidationTerminal:
    """Tests for render_validation_terminal."""

    def test_renders_verified(self):
        result = ValidationResult(verified=3, unverified=0)
        output = render_validation_terminal(result)
        assert_that(output).contains("3 verified")

    def test_renders_unverified_with_details(self):
        result = ValidationResult(
            verified=1,
            unverified=2,
            details=[
                "[B101] main.py:10 — issue still present",
                "[E501] utils.py:25 — issue still present",
            ],
        )
        output = render_validation_terminal(result)
        assert_that(output).contains("1 verified")
        assert_that(output).contains("2 unverified")
        assert_that(output).contains("B101")
        assert_that(output).contains("E501")

    def test_empty_result_returns_empty(self):
        result = ValidationResult()
        output = render_validation_terminal(result)
        assert_that(output).is_empty()
