"""Tests for lint bridge integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.review.lint_bridge import (
    format_lint_results_for_prompt,
    run_lint_on_changed_files,
)
from lintro.config.lintro_config import LintroConfig
from lintro.models.core.tool_result import ToolResult


def test_run_lint_on_changed_files_returns_empty_for_no_paths() -> None:
    """No changed files yields an empty lint result list."""
    results = run_lint_on_changed_files(
        changed_files=[],
        lintro_config=LintroConfig(),
    )

    assert_that(results).is_empty()


def test_format_lint_results_for_prompt_returns_empty_without_issues() -> None:
    """Empty lint results produce an empty prompt section."""
    digest = format_lint_results_for_prompt(
        results=[ToolResult(name="ruff", success=True, issues_count=0, issues=[])],
    )

    assert_that(digest).is_empty()


def test_format_lint_results_for_prompt_wraps_issue_lines() -> None:
    """Lint issues are formatted as compact digest lines."""
    issue = MagicMock()
    issue.code = "E501"
    issue.message = "Line too long"
    issue.file = "src/main.py"
    issue.line = 10

    digest = format_lint_results_for_prompt(
        results=[
            ToolResult(
                name="ruff",
                success=False,
                issues_count=1,
                issues=[issue],
            ),
        ],
    )

    assert_that(digest).contains("<lint_results>")
    assert_that(digest).contains("Tool: ruff")
    assert_that(digest).contains("E501")


def test_run_lint_on_changed_files_invokes_tool_check() -> None:
    """Lint bridge runs configured tools against changed file paths."""
    mock_tool = MagicMock()
    mock_tool.check.return_value = ToolResult(name="ruff", success=True, issues_count=0)

    with patch(
        "lintro.ai.review.lint_bridge.get_tools_to_run",
    ) as mock_get_tools:
        mock_get_tools.return_value.to_run = ["ruff"]
        with (
            patch(
                "lintro.ai.review.lint_bridge.tool_manager.get_tool",
                return_value=mock_tool,
            ),
            patch(
                "lintro.ai.review.lint_bridge.configure_tool_for_execution",
            ),
        ):
            results = run_lint_on_changed_files(
                changed_files=["src/main.py"],
                lintro_config=LintroConfig(),
            )

    assert_that(results).is_length(1)
    mock_tool.check.assert_called_once()
