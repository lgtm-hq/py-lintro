"""Tests for GitHub review posting adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.review.github import (
    format_finding_comment,
    format_review_summary,
    post_review_to_github,
)
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult


def test_format_finding_comment_includes_severity_and_fix(
    sample_review_result: ReviewResult,
) -> None:
    """Finding comment includes severity badge and fix suggestion."""
    finding = sample_review_result.findings[0]
    comment = format_finding_comment(finding=finding)

    assert_that(comment).contains("P1")
    assert_that(comment).contains("Default to Expired")


def test_format_review_summary_includes_checklist_table(
    sample_review_result: ReviewResult,
) -> None:
    """Summary comment includes checklist table rows."""
    summary = format_review_summary(result=sample_review_result)

    assert_that(summary).contains("## Lintro Review")
    assert_that(summary).contains("| 1 | yes |")
    assert_that(summary).does_not_contain("Findings outside diff")


def test_post_review_to_github_posts_fallback_without_line_number(
    sample_review_result: ReviewResult,
) -> None:
    """Fallback comments omit invalid line numbers from the location header."""
    outside_diff_finding = ReviewFinding(
        severity="P2",
        category="architecture",
        file="docs/design.md",
        line=0,
        title="Missing ADR",
        description="No architecture decision record",
        cause="Process gap",
        fix="Add ADR",
        confidence="medium",
        checklist_ids=(),
    )
    result = ReviewResult(
        metadata=sample_review_result.metadata,
        summary=sample_review_result.summary,
        checklist=sample_review_result.checklist,
        findings=(*sample_review_result.findings, outside_diff_finding),
    )
    reporter = MagicMock()
    reporter.is_available.return_value = True
    reporter._fetch_pr_diff_lines.return_value = {"src/main.py": {10}}
    reporter._post_issue_comment.return_value = True

    with patch(
        "lintro.ai.review.github._post_inline_findings",
        return_value=True,
    ):
        post_review_to_github(result=result, reporter=reporter)

    fallback_calls = [
        call.args[0]
        for call in reporter._post_issue_comment.call_args_list
        if call.args[0].startswith("`docs/design.md`")
    ]
    assert_that(fallback_calls).is_length(1)
    assert_that(fallback_calls[0]).does_not_contain(":0")


def test_post_review_to_github_returns_false_when_unavailable(
    sample_review_result: ReviewResult,
) -> None:
    """Posting is skipped cleanly when GitHub context is unavailable."""
    reporter = MagicMock()
    reporter.is_available.return_value = False

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
    )

    assert_that(posted).is_false()
    reporter._post_issue_comment.assert_not_called()


def test_post_review_to_github_posts_summary_and_inline(
    sample_review_result: ReviewResult,
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
