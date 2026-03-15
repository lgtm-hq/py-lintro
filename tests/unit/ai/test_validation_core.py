"""Tests for core validation logic and rendering.

Covers _validate_suggestions, verify_fixes, and render_validation_terminal.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.display import render_validation_terminal
from lintro.ai.models import AIFixSuggestion
from lintro.ai.validation import (
    ValidationResult,
    _validate_suggestions,
    verify_fixes,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue

from .conftest import MockIssue


def _make_suggestion(
    *,
    file: str = "src/main.py",
    line: int = 10,
    code: str = "B101",
    tool_name: str = "ruff",
) -> AIFixSuggestion:
    """Create an AIFixSuggestion for testing."""
    return AIFixSuggestion(
        file=file,
        line=line,
        code=code,
        tool_name=tool_name,
        original_code="assert x",
        suggested_code="if not x: raise",
        explanation="Replace assert",
    )


# -- render_validation_terminal ------------------------------------------------


def test_render_validation_terminal_renders_verified():
    """Verify terminal rendering displays the verified fix count."""
    result = ValidationResult(verified=3, unverified=0)
    output = render_validation_terminal(result)
    assert_that(output).contains("3 resolved")


def test_render_validation_terminal_renders_unverified_with_details():
    """Verify terminal rendering shows unverified count and detail lines."""
    result = ValidationResult(
        verified=1,
        unverified=2,
        details=[
            "[B101] main.py:10 — issue still present",
            "[E501] utils.py:25 — issue still present",
        ],
    )
    output = render_validation_terminal(result)
    assert_that(output).contains("1 resolved")
    assert_that(output).contains("2 still present")
    assert_that(output).contains("B101")
    assert_that(output).contains("E501")


def test_render_validation_terminal_empty_result_returns_empty():
    """Verify an empty ValidationResult produces empty terminal output."""
    result = ValidationResult()
    output = render_validation_terminal(result)
    assert_that(output).is_empty()


# -- _validate_suggestions (shared core logic) --------------------------------


def test_validate_suggestions_verified_when_no_remaining_issues():
    """Core validation marks fix as verified when no issues remain for the tool."""
    suggestion = _make_suggestion(tool_name="ruff", code="B101")
    result = _validate_suggestions([suggestion], {"ruff": []})

    assert_that(result.verified).is_equal_to(1)
    assert_that(result.unverified).is_equal_to(0)


def test_validate_suggestions_unverified_when_issue_remains():
    """Core validation marks fix as unverified when the issue still appears."""
    remaining = MagicMock()
    remaining.file = "src/main.py"
    remaining.code = "B101"
    remaining.line = 10

    suggestion = _make_suggestion(tool_name="ruff", code="B101", line=10)
    result = _validate_suggestions([suggestion], {"ruff": [remaining]})

    assert_that(result.verified).is_equal_to(0)
    assert_that(result.unverified).is_equal_to(1)


def test_validate_suggestions_skips_tool_without_fresh_issues():
    """When a tool has no entry in fresh_issues_by_tool, its suggestions are skipped."""
    suggestion = _make_suggestion(tool_name="ruff")
    result = _validate_suggestions([suggestion], {})

    assert_that(result.verified).is_equal_to(0)
    assert_that(result.unverified).is_equal_to(0)


# -- verify_fixes (unified entry point) ---------------------------------------


def test_verify_fixes_returns_none_for_empty_suggestions():
    """verify_fixes returns None when given an empty suggestions list."""
    result = verify_fixes(applied_suggestions=[], by_tool={})
    assert_that(result).is_none()


@patch("lintro.ai.rerun.rerun_tools")
@patch("lintro.ai.rerun.apply_rerun_results")
def test_verify_fixes_runs_tools_once_and_validates(
    mock_apply_rerun,
    mock_rerun_tools,
):
    """verify_fixes calls rerun_tools once and produces a ValidationResult."""
    # Set up a fresh rerun result with no remaining issues
    fresh_result = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        issues=[],
    )
    mock_rerun_tools.return_value = [fresh_result]

    suggestion = _make_suggestion(tool_name="ruff")
    issue = MockIssue(
        file="src/main.py",
        line=10,
        column=1,
        message="test",
        code="B101",
        severity="low",
    )
    original_result = ToolResult(name="ruff", success=False, issues_count=1)
    issues: list[BaseIssue] = [issue]
    by_tool = {"ruff": (original_result, issues)}

    result = verify_fixes(
        applied_suggestions=[suggestion],
        by_tool=by_tool,
    )

    assert_that(result).is_not_none()
    assert_that(result.verified).is_equal_to(1)  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    assert_that(result.unverified).is_equal_to(0)  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    # rerun_tools should be called exactly once (not twice as before)
    mock_rerun_tools.assert_called_once_with(by_tool)
    mock_apply_rerun.assert_called_once()


@patch("lintro.ai.rerun.rerun_tools")
@patch("lintro.ai.rerun.apply_rerun_results")
def test_verify_fixes_updates_tool_results_and_validates(
    mock_apply_rerun,
    mock_rerun_tools,
):
    """verify_fixes both updates ToolResults (via apply_rerun_results) and validates."""
    remaining = MagicMock()
    remaining.file = "src/main.py"
    remaining.code = "B101"
    remaining.line = 10

    fresh_result = ToolResult(
        name="ruff",
        success=True,
        issues_count=1,
        issues=[remaining],
    )
    mock_rerun_tools.return_value = [fresh_result]

    suggestion = _make_suggestion(tool_name="ruff", code="B101", line=10)
    issue = MockIssue(
        file="src/main.py",
        line=10,
        column=1,
        message="test",
        code="B101",
        severity="low",
    )
    original_result = ToolResult(name="ruff", success=False, issues_count=1)
    issues: list[BaseIssue] = [issue]
    by_tool = {"ruff": (original_result, issues)}

    result = verify_fixes(
        applied_suggestions=[suggestion],
        by_tool=by_tool,
    )

    assert_that(result).is_not_none()
    assert_that(result.unverified).is_equal_to(1)  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    assert_that(result.verified).is_equal_to(0)  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    # Confirms apply_rerun_results was called to update ToolResult objects
    mock_apply_rerun.assert_called_once()


@patch("lintro.ai.rerun.rerun_tools")
def test_verify_fixes_handles_no_rerun_results(mock_rerun_tools):
    """verify_fixes handles the case where rerun_tools returns None."""
    mock_rerun_tools.return_value = None

    suggestion = _make_suggestion(tool_name="ruff")
    issue = MockIssue(
        file="src/main.py",
        line=10,
        column=1,
        message="test",
        code="B101",
        severity="low",
    )
    original_result = ToolResult(name="ruff", success=False, issues_count=1)
    issues: list[BaseIssue] = [issue]
    by_tool = {"ruff": (original_result, issues)}

    result = verify_fixes(
        applied_suggestions=[suggestion],
        by_tool=by_tool,
    )

    # With no rerun results, verify_fixes returns None
    assert_that(result).is_none()
