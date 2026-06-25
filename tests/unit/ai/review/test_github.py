"""Tests for GitHub review posting adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.review.github import (
    format_finding_comment,
    format_review_summary,
    post_review_to_github,
)


def test_format_finding_comment_includes_severity_and_fix(
    sample_review_result,
) -> None:
    """Finding comment includes severity badge and fix suggestion."""
    finding = sample_review_result.findings[0]
    comment = format_finding_comment(finding=finding)

    assert_that(comment).contains("P1")
    assert_that(comment).contains("Default to Expired")


def test_format_review_summary_includes_checklist_table(
    sample_review_result,
) -> None:
    """Summary comment includes checklist table rows."""
    summary = format_review_summary(result=sample_review_result)

    assert_that(summary).contains("## Lintro Review")
    assert_that(summary).contains("| 1 | yes |")


def test_post_review_to_github_returns_false_when_unavailable(
    sample_review_result,
) -> None:
    """Posting is skipped cleanly when GitHub context is unavailable."""
    reporter = MagicMock()
    reporter.is_available.return_value = False

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
    )

    assert_that(posted).is_false()


def test_post_review_to_github_posts_summary_and_inline(
    sample_review_result,
) -> None:
    """Available reporter posts summary and inline review comments."""
    reporter = MagicMock()
    reporter.is_available.return_value = True
    reporter._fetch_pr_diff_lines.return_value = {"src/main.py": {10}}
    reporter._post_issue_comment.return_value = True
    reporter._api_request.return_value = True

    with patch(
        "lintro.ai.review.github._post_inline_findings",
        return_value=True,
    ) as mock_inline:
        posted = post_review_to_github(
            result=sample_review_result,
            reporter=reporter,
        )

    assert_that(posted).is_true()
    assert_that(reporter._post_issue_comment.called).is_true()
    assert_that(mock_inline.called).is_true()
