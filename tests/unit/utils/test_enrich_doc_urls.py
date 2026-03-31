"""Tests for _enrich_issues_with_doc_urls orchestration function.

Verifies that doc_url enrichment works correctly for all edge cases:
tools with/without doc_url support, issues with/without existing URLs,
and issues with empty codes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.tool_executor import _enrich_issues_with_doc_urls


def _make_issue(*, code: str = "", doc_url: str = "") -> BaseIssue:
    """Create a BaseIssue with the given code and doc_url.

    Args:
        code: Rule code for the issue.
        doc_url: Pre-existing doc_url value.

    Returns:
        A BaseIssue instance.
    """
    issue = BaseIssue(file="test.py", line=1, message="test")
    issue.code = code  # type: ignore[attr-defined]
    issue.doc_url = doc_url
    return issue


def test_tool_without_doc_url_method_is_noop() -> None:
    """Tools without a doc_url method should not modify issues."""
    tool = MagicMock(spec=[])  # no doc_url attribute
    issue = _make_issue(code="E501")
    result = ToolResult(name="test", success=False, output="", issues=[issue])

    _enrich_issues_with_doc_urls(tool, result)

    assert_that(issue.doc_url).is_empty()


def test_result_with_no_issues_is_noop() -> None:
    """Results with no issues should not call doc_url."""
    tool = MagicMock()
    tool.doc_url.return_value = "https://example.com"
    result = ToolResult(name="test", success=True, output="", issues=[])

    _enrich_issues_with_doc_urls(tool, result)

    tool.doc_url.assert_not_called()


def test_issue_with_existing_doc_url_not_overwritten() -> None:
    """Issues that already have a doc_url should be skipped."""
    tool = MagicMock()
    tool.doc_url.return_value = "https://new.com"
    issue = _make_issue(code="E501", doc_url="https://existing.com")
    result = ToolResult(name="test", success=False, output="", issues=[issue])

    _enrich_issues_with_doc_urls(tool, result)

    assert_that(issue.doc_url).is_equal_to("https://existing.com")
    tool.doc_url.assert_not_called()


def test_issue_with_empty_code_skipped() -> None:
    """Issues with empty code should not trigger doc_url lookup."""
    tool = MagicMock()
    tool.doc_url.return_value = "https://example.com"
    issue = _make_issue(code="")
    result = ToolResult(name="test", success=False, output="", issues=[issue])

    _enrich_issues_with_doc_urls(tool, result)

    assert_that(issue.doc_url).is_empty()
    tool.doc_url.assert_not_called()


def test_happy_path_doc_url_populated() -> None:
    """Issues with a code get their doc_url populated from the tool."""
    tool = MagicMock()
    tool.doc_url.return_value = "https://example.com/E501"
    issue = _make_issue(code="E501")
    result = ToolResult(name="test", success=False, output="", issues=[issue])

    _enrich_issues_with_doc_urls(tool, result)

    assert_that(issue.doc_url).is_equal_to("https://example.com/E501")
    tool.doc_url.assert_called_once_with("E501")


def test_tool_returns_none_leaves_doc_url_empty() -> None:
    """When tool.doc_url returns None, issue.doc_url stays empty."""
    tool = MagicMock()
    tool.doc_url.return_value = None
    issue = _make_issue(code="UNKNOWN")
    result = ToolResult(name="test", success=False, output="", issues=[issue])

    _enrich_issues_with_doc_urls(tool, result)

    assert_that(issue.doc_url).is_empty()


def test_multiple_mixed_issues() -> None:
    """Only eligible issues get enriched; others are left unchanged."""
    tool = MagicMock()
    tool.doc_url.side_effect = lambda code: f"https://example.com/{code}"

    issue_with_url = _make_issue(code="E501", doc_url="https://existing.com")
    issue_empty_code = _make_issue(code="")
    issue_eligible = _make_issue(code="F401")

    result = ToolResult(
        name="test",
        success=False,
        output="",
        issues=[issue_with_url, issue_empty_code, issue_eligible],
    )

    _enrich_issues_with_doc_urls(tool, result)

    assert_that(issue_with_url.doc_url).is_equal_to("https://existing.com")
    assert_that(issue_empty_code.doc_url).is_empty()
    assert_that(issue_eligible.doc_url).is_equal_to("https://example.com/F401")
    tool.doc_url.assert_called_once_with("F401")
