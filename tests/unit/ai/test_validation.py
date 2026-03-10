"""Tests for AI fix validation."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.display import render_validation_terminal
from lintro.ai.models import AIFixSuggestion
from lintro.ai.validation import (
    ValidationResult,
    _validate_suggestions,
    validate_applied_fixes,
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
    return AIFixSuggestion(
        file=file,
        line=line,
        code=code,
        tool_name=tool_name,
        original_code="assert x",
        suggested_code="if not x: raise",
        explanation="Replace assert",
    )


# -- validate_applied_fixes ---------------------------------------------------


def test_validate_applied_fixes_returns_none_for_empty():
    """Verify validation returns None when given an empty suggestions list."""
    result = validate_applied_fixes([])
    assert_that(result).is_none()


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_verified_when_issue_gone(mock_check):
    """Verify a fix is marked as verified when the tool reports no remaining issues."""
    mock_check.return_value = []  # No issues remain
    suggestion = _make_suggestion()

    result = validate_applied_fixes([suggestion])

    assert result is not None
    assert_that(result.verified).is_equal_to(1)
    assert_that(result.unverified).is_equal_to(0)


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_unverified_when_issue_remains(mock_check):
    """Fix is marked unverified when issue still appears in output."""
    remaining = MagicMock()
    remaining.file = "src/main.py"
    remaining.code = "B101"
    remaining.line = 10
    mock_check.return_value = [remaining]

    suggestion = _make_suggestion()
    result = validate_applied_fixes([suggestion])

    assert result is not None
    assert_that(result.verified).is_equal_to(0)
    assert_that(result.unverified).is_equal_to(1)
    assert_that(result.details).is_length(1)


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_mixed_verified_and_unverified(mock_check):
    """Verify correct counts when some fixes are verified and others are not."""
    remaining = MagicMock()
    remaining.file = "src/main.py"
    remaining.code = "B101"
    remaining.line = 10
    mock_check.return_value = [remaining]

    s1 = _make_suggestion(code="B101")
    s2 = _make_suggestion(code="E501")  # This one is resolved

    result = validate_applied_fixes([s1, s2])

    assert result is not None
    assert_that(result.verified).is_equal_to(1)
    assert_that(result.unverified).is_equal_to(1)
    assert_that(result.verified_by_tool.get("ruff")).is_equal_to(1)
    assert_that(result.unverified_by_tool.get("ruff")).is_equal_to(1)


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_matches_by_line_before_file_code(mock_check):
    """Validation matches remaining issues by line, not just file."""
    remaining = MagicMock()
    remaining.file = "src/main.py"
    remaining.code = "E501"
    remaining.line = 20
    mock_check.return_value = [remaining]

    resolved = _make_suggestion(code="E501", line=10)
    unresolved = _make_suggestion(code="E501", line=20)

    result = validate_applied_fixes([resolved, unresolved])

    assert result is not None
    assert_that(result.verified).is_equal_to(1)
    assert_that(result.unverified).is_equal_to(1)
    assert_that(result.details).is_length(1)
    assert_that(result.details[0]).contains("main.py:20")


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_unknown_remaining_line_marks_issue_unverified(
    mock_check,
):
    """Verify a remaining issue with unknown line number marks the fix as unverified."""
    remaining = MagicMock()
    remaining.file = "src/main.py"
    remaining.code = "E501"
    remaining.line = None
    mock_check.return_value = [remaining]

    suggestion = _make_suggestion(code="E501", line=30)
    result = validate_applied_fixes([suggestion])

    assert result is not None
    assert_that(result.verified).is_equal_to(0)
    assert_that(result.unverified).is_equal_to(1)


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_skips_unknown_tool(mock_check):
    """Suggestions with unknown tool names are skipped silently."""
    suggestion = _make_suggestion(tool_name="unknown")
    result = validate_applied_fixes([suggestion])

    # No tool actually ran, so validate_applied_fixes returns None.
    assert result is None
    mock_check.assert_not_called()


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_skips_when_check_returns_none(mock_check):
    """Verify None is returned when all tool checks return None."""
    mock_check.return_value = None  # Tool not available
    suggestion = _make_suggestion()

    result = validate_applied_fixes([suggestion])

    # No tool successfully ran, so validate_applied_fixes returns None.
    assert result is None


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_groups_by_tool(mock_check):
    """Verify validation groups suggestions by tool and checks each tool separately."""
    mock_check.return_value = []

    s1 = _make_suggestion(tool_name="ruff")
    s2 = _make_suggestion(tool_name="mypy", code="error")

    result = validate_applied_fixes([s1, s2])

    assert result is not None
    assert_that(result.verified).is_equal_to(2)
    assert_that(mock_check.call_count).is_equal_to(2)


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_matches_relative_remaining_paths_against_absolute_fixes(
    mock_check,
    tmp_path,
    monkeypatch,
):
    """Relative remaining paths match against absolute fix paths."""
    project_file = tmp_path / "src" / "main.py"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("print('ok')\n")

    monkeypatch.chdir(tmp_path)

    remaining = MagicMock()
    remaining.file = os.path.join("src", "main.py")
    remaining.code = "B101"
    mock_check.return_value = [remaining]

    suggestion = _make_suggestion(file=str(project_file.resolve()), code="B101")
    result = validate_applied_fixes([suggestion])

    assert result is not None
    assert_that(result.unverified).is_equal_to(1)
    assert_that(result.verified).is_equal_to(0)


@patch("lintro.ai.validation._run_tool_check")
def test_validate_applied_fixes_tracks_new_issues(mock_check):
    """Leftover remaining_counts after matching become new_issues."""
    remaining_a = MagicMock()
    remaining_a.file = "src/main.py"
    remaining_a.code = "W123"
    remaining_a.line = 5
    remaining_b = MagicMock()
    remaining_b.file = "src/main.py"
    remaining_b.code = "B101"
    remaining_b.line = 10
    mock_check.return_value = [remaining_a, remaining_b]

    # Only one suggestion matches B101 -- W123 is new/unrelated
    suggestion = _make_suggestion(code="B101", line=10)
    result = validate_applied_fixes([suggestion])

    assert result is not None
    assert_that(result.unverified).is_equal_to(1)
    assert_that(result.new_issues).is_equal_to(1)


# -- _run_tool_check ----------------------------------------------------------


@patch("lintro.tools.tool_manager.get_tool")
def test_run_tool_check_returns_issues(mock_get_tool):
    """Verify _run_tool_check returns the list of issues from a successful tool run."""
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
def test_run_tool_check_returns_none_on_error(mock_get_tool):
    """Verify _run_tool_check returns None when the tool raises an exception."""
    from lintro.ai.validation import _run_tool_check

    mock_tool = MagicMock()
    mock_tool.check.side_effect = RuntimeError("fail")
    mock_get_tool.return_value = mock_tool

    issues = _run_tool_check("ruff", ["src/main.py"])
    assert_that(issues).is_none()


@patch("lintro.tools.tool_manager.get_tool")
def test_run_tool_check_returns_none_for_missing_tool(mock_get_tool):
    """Verify _run_tool_check returns None when the requested tool does not exist."""
    from lintro.ai.validation import _run_tool_check

    mock_get_tool.side_effect = KeyError("no such tool")

    issues = _run_tool_check("nonexistent", ["src/main.py"])
    assert_that(issues).is_none()


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

    assert result is not None
    assert_that(result.verified).is_equal_to(1)
    assert_that(result.unverified).is_equal_to(0)
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

    assert result is not None
    assert_that(result.unverified).is_equal_to(1)
    assert_that(result.verified).is_equal_to(0)
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
    assert result is None
