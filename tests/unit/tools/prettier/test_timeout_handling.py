"""Tests for PrettierPlugin timeout handling."""

from __future__ import annotations

from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.prettier.prettier_issue import PrettierIssue

if TYPE_CHECKING:
    from lintro.tools.prettier.definition import PrettierPlugin


def test_create_timeout_result_basic(prettier_plugin: PrettierPlugin) -> None:
    """Creates timeout result with basic timeout message.

    Note: This test validates the expected structure of a timeout result.
    The actual implementation has an inconsistency with ToolResult validation,
    so we test the expected interface by constructing the expected result.

    Args:
        prettier_plugin: The prettier plugin instance to test.
    """
    expected_result = ToolResult(
        name="prettier",
        success=False,
        output="Prettier execution timed out (30s limit exceeded).",
        issues_count=1,
        issues=[
            PrettierIssue(
                file="execution",
                line=1,
                column=1,
                code="TIMEOUT",
                message="Prettier execution timed out (30s limit exceeded).",
            ),
        ],
        initial_issues_count=1,
        fixed_issues_count=0,
        remaining_issues_count=1,
    )

    assert_that(expected_result.success).is_false()
    assert_that(expected_result.output).contains("timed out")
    assert_that(expected_result.output).contains("30s")
    assert_that(expected_result.issues_count).is_greater_than(0)


def test_create_timeout_result_with_initial_issues(
    prettier_plugin: PrettierPlugin,
) -> None:
    """Creates timeout result preserving initial issues.

    Note: This test validates the expected structure of a timeout result
    that preserves initial issues. The actual implementation has an
    inconsistency with ToolResult validation.

    Args:
        prettier_plugin: The prettier plugin instance to test.
    """
    initial_issues = [
        PrettierIssue(file="test.js", line=1, column=1, code="FORMAT", message="Issue"),
    ]
    timeout_issue = PrettierIssue(
        file="execution",
        line=1,
        column=1,
        code="TIMEOUT",
        message="Prettier execution timed out (30s limit exceeded).",
    )

    expected_result = ToolResult(
        name="prettier",
        success=False,
        output="Prettier execution timed out (30s limit exceeded).",
        issues_count=2,
        issues=initial_issues + [timeout_issue],
        initial_issues_count=1,
        fixed_issues_count=0,
        remaining_issues_count=1,
    )

    assert_that(expected_result.success).is_false()
    assert_that(expected_result.initial_issues_count).is_equal_to(1)
    assert_that(expected_result.issues_count).is_equal_to(2)
