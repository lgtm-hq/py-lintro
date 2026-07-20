"""Tests for issue category taxonomy helpers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.issue_category import IssueCategory
from lintro.enums.tool_type import ToolType
from lintro.parsers.base_issue import BaseIssue
from lintro.parsers.oxlint.oxlint_issue import OxlintIssue
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.utils.issue_category import (
    category_from_rule_code,
    category_from_tool_type,
    enrich_issue_category,
    resolve_issue_category,
)


def test_category_from_rule_code_accessibility() -> None:
    """Oxlint jsx-a11y rule codes map to Accessibility."""
    assert_that(category_from_rule_code("jsx-a11y/alt-text")).is_equal_to(
        IssueCategory.ACCESSIBILITY,
    )
    assert_that(
        category_from_rule_code("eslint(jsx-a11y/anchor-is-valid)"),
    ).is_equal_to(
        IssueCategory.ACCESSIBILITY,
    )


def test_category_from_rule_code_performance() -> None:
    """Ruff PERF/complexity codes and oxlint perf rules map to Performance."""
    assert_that(category_from_rule_code("PERF401")).is_equal_to(
        IssueCategory.PERFORMANCE,
    )
    assert_that(category_from_rule_code("C901")).is_equal_to(IssueCategory.PERFORMANCE)
    assert_that(category_from_rule_code("PLR0915")).is_equal_to(
        IssueCategory.PERFORMANCE,
    )
    assert_that(
        category_from_rule_code("eslint(perf/no-accumulate-spread)"),
    ).is_equal_to(
        IssueCategory.PERFORMANCE,
    )


def test_category_from_tool_type_fallbacks() -> None:
    """ToolType flags map to the expected concern categories."""
    assert_that(category_from_tool_type(ToolType.SECURITY)).is_equal_to(
        IssueCategory.SECURITY,
    )
    assert_that(category_from_tool_type(ToolType.TYPE_CHECKER)).is_equal_to(
        IssueCategory.CORRECTNESS,
    )
    assert_that(category_from_tool_type(ToolType.FORMATTER)).is_equal_to(
        IssueCategory.STYLE,
    )
    assert_that(category_from_tool_type(ToolType.DOCUMENTATION)).is_equal_to(
        IssueCategory.DOCUMENTATION,
    )
    assert_that(category_from_tool_type(ToolType.INFRASTRUCTURE)).is_equal_to(
        IssueCategory.INFRASTRUCTURE,
    )
    assert_that(category_from_tool_type(ToolType.LINTER)).is_equal_to(
        IssueCategory.CORRECTNESS,
    )


def test_resolve_prefers_existing_category() -> None:
    """Existing issue.category wins over heuristics."""
    issue = BaseIssue(file="a.py", line=1, message="x", category="security")
    assert_that(resolve_issue_category(issue, tool_name="ruff")).is_equal_to(
        IssueCategory.SECURITY,
    )


def test_resolve_uses_tool_name_defaults() -> None:
    """Known tools fall back to taxonomy defaults when codes are unhelpful."""
    issue = RuffIssue(file="a.py", line=1, code="E501", message="long")
    bandit_issue = BaseIssue(file="a.py", line=1, message="hardcoded")
    assert_that(resolve_issue_category(bandit_issue, tool_name="bandit")).is_equal_to(
        IssueCategory.SECURITY,
    )
    assert_that(
        resolve_issue_category(BaseIssue(message="x"), tool_name="markdownlint"),
    ).is_equal_to(IssueCategory.DOCUMENTATION)
    assert_that(
        resolve_issue_category(BaseIssue(message="x"), tool_name="shellcheck"),
    ).is_equal_to(IssueCategory.INFRASTRUCTURE)
    assert_that(issue.code).is_equal_to("E501")


def test_enrich_issue_category_persists_title_case() -> None:
    """Enrichment writes the title-case category onto the issue."""
    issue = OxlintIssue(
        file="a.tsx",
        line=1,
        code="jsx-a11y/alt-text",
        message="missing alt",
    )
    category = enrich_issue_category(issue, tool_name="oxlint")
    assert_that(category).is_equal_to(IssueCategory.ACCESSIBILITY)
    assert_that(issue.category).is_equal_to("Accessibility")
