"""Tests for how inline comments are anchored and scoped to a round (#1911)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.github import post_review_to_github
from lintro.ai.review.github_sticky import build_sticky_comment
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.suggested_change import SuggestedChange

_TEST_TOKEN = (
    "ghp_test_fixture_token"  # noqa: S105  # nosec B105 — fake test fixture token
)


def _reporter() -> MagicMock:
    """Build a reporter stub whose PR and round diffs both cover src/main.py:10.

    Returns:
        The stub reporter.
    """
    reporter = MagicMock()
    reporter.is_available.return_value = True
    reporter.find_issue_comment.return_value = None
    reporter.fetch_pr_diff_lines.return_value = {"src/main.py": {9, 10, 11}}
    reporter.fetch_compare_lines.return_value = {"src/main.py": {9, 10, 11}}
    reporter.fetch_pr_commit_shas.return_value = []
    reporter.post_issue_comment.return_value = True
    reporter.update_issue_comment.return_value = True
    reporter.api_request.return_value = True
    reporter.api_base = "https://api.github.com"
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


def _with_change(
    *,
    result: ReviewResult,
    change: SuggestedChange | None,
) -> ReviewResult:
    """Attach a suggested change to the result's first finding.

    Args:
        result: Review result to adapt.
        change: Change to attach.

    Returns:
        The adapted result.
    """
    first = replace(result.findings[0], suggested_change=change)
    return replace(result, findings=(first, *result.findings[1:]))


def _inline_comments(*, reporter: MagicMock) -> list[dict[str, Any]]:
    """Extract the inline comments from the submitted review payload.

    Args:
        reporter: Reporter stub the review was posted through.

    Returns:
        The ``comments`` array of the review payload.
    """
    payload = reporter.api_request.call_args.args[2]
    comments: list[dict[str, Any]] = payload["comments"]
    return comments


def test_single_line_mode_a_anchors_to_the_suggestion_line(
    sample_review_result: ReviewResult,
) -> None:
    """The anchor is exactly the replaced line, so the suggestion is valid."""
    reporter = _reporter()
    result = _with_change(
        result=sample_review_result,
        change=SuggestedChange(start_line=10, end_line=10, replacement="ok"),
    )

    post_review_to_github(result=result, reporter=reporter)

    comment = _inline_comments(reporter=reporter)[0]
    assert_that(comment["line"]).is_equal_to(10)
    assert_that(comment).does_not_contain_key("start_line")
    assert_that(comment["body"]).contains("```suggestion")


def test_multiline_mode_a_anchors_the_whole_replaced_range(
    sample_review_result: ReviewResult,
) -> None:
    """A multi-line suggestion must cover every line its comment spans."""
    reporter = _reporter()
    result = _with_change(
        result=sample_review_result,
        change=SuggestedChange(start_line=9, end_line=11, replacement="a\nb\nc"),
    )

    post_review_to_github(result=result, reporter=reporter)

    comment = _inline_comments(reporter=reporter)[0]
    assert_that(comment["start_line"]).is_equal_to(9)
    assert_that(comment["start_side"]).is_equal_to("RIGHT")
    assert_that(comment["line"]).is_equal_to(11)


def test_mode_b_anchors_to_the_findings_own_line(
    sample_review_result: ReviewResult,
) -> None:
    """Without a committable suggestion the anchor stays a single line."""
    reporter = _reporter()
    result = _with_change(result=sample_review_result, change=None)

    post_review_to_github(result=result, reporter=reporter)

    comment = _inline_comments(reporter=reporter)[0]
    assert_that(comment["line"]).is_equal_to(10)
    assert_that(comment).does_not_contain_key("start_line")
    assert_that(comment["body"]).does_not_contain("```suggestion")


def test_round_two_scopes_suggestions_to_the_new_commits(
    sample_review_result: ReviewResult,
) -> None:
    """A line the PR changed earlier but this round did not is not committable."""
    reporter = _reporter()
    result = _with_change(
        result=sample_review_result,
        change=SuggestedChange(start_line=10, end_line=10, replacement="ok"),
    )
    reporter.find_issue_comment.return_value = (
        42,
        build_sticky_comment(result=result, head_sha="aaa111"),
    )
    # This round only touched line 40, so the finding's line is old ground.
    reporter.fetch_compare_lines.return_value = {"src/main.py": {40}}

    post_review_to_github(result=result, reporter=reporter)

    assert_that(reporter.fetch_compare_lines.called).is_true()
    assert_that(_inline_comments(reporter=reporter)[0]["body"]).does_not_contain(
        "```suggestion",
    )


def test_round_one_treats_the_whole_pr_diff_as_this_rounds_diff(
    sample_review_result: ReviewResult,
) -> None:
    """With no prior round there is nothing to compare against."""
    reporter = _reporter()
    result = _with_change(
        result=sample_review_result,
        change=SuggestedChange(start_line=10, end_line=10, replacement="ok"),
    )

    post_review_to_github(result=result, reporter=reporter)

    assert_that(reporter.fetch_compare_lines.called).is_false()
    assert_that(_inline_comments(reporter=reporter)[0]["body"]).contains(
        "```suggestion",
    )


def test_carried_over_finding_falls_back_to_mode_b(
    sample_review_result: ReviewResult,
) -> None:
    """A finding already raised in round 1 keeps its thread, loses its one-click fix."""
    reporter = _reporter()
    result = _with_change(
        result=sample_review_result,
        change=SuggestedChange(start_line=10, end_line=10, replacement="ok"),
    )
    # Round 1 recorded the same finding; round 2 reports it again on a line the
    # new commits did touch, so only the carried-over rule can reject it.
    reporter.find_issue_comment.return_value = (
        42,
        build_sticky_comment(result=result, head_sha="aaa111"),
    )
    reporter.fetch_compare_lines.return_value = {"src/main.py": {9, 10, 11}}

    post_review_to_github(result=result, reporter=reporter)

    body = _inline_comments(reporter=reporter)[0]["body"]
    assert_that(body).does_not_contain("```suggestion")
    assert_that(body).contains("**Fix:**")


def test_round_two_finding_new_to_this_round_keeps_mode_a(
    sample_review_result: ReviewResult,
) -> None:
    """Control for the carried-over rule: only being *carried* rejects it."""
    reporter = _reporter()
    prior = _with_change(
        result=sample_review_result,
        change=SuggestedChange(start_line=10, end_line=10, replacement="ok"),
    )
    reporter.find_issue_comment.return_value = (
        42,
        build_sticky_comment(result=prior, head_sha="aaa111"),
    )
    reporter.fetch_compare_lines.return_value = {"src/main.py": {9, 10, 11}}
    # Same location, different finding — round 2 sees it for the first time.
    first = replace(prior.findings[0], title="A different defect entirely")
    result = replace(prior, findings=(first, *prior.findings[1:]))

    post_review_to_github(result=result, reporter=reporter)

    assert_that(_inline_comments(reporter=reporter)[0]["body"]).contains(
        "```suggestion",
    )


# --- compare endpoint -------------------------------------------------------


def _reader(payload: dict[str, Any]) -> MagicMock:
    """Build a urlopen context manager yielding ``payload`` as JSON.

    Args:
        payload: Response body.

    Returns:
        The context-manager mock.
    """
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    ctx = MagicMock()
    ctx.__enter__.return_value = response
    return ctx


def test_fetch_compare_lines_parses_the_comparison_patches() -> None:
    """The comparison's patches reduce to right-side line numbers."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)
    payload = {
        "files": [
            {
                "filename": "src/main.py",
                "patch": "@@ -1,2 +1,3 @@\n ctx\n+added\n+also",
            },
        ],
    }

    with patch("urllib.request.urlopen", return_value=_reader(payload)):
        lines = reporter.fetch_compare_lines(base="aaa111", head="bbb222")

    assert_that(lines).is_equal_to({"src/main.py": {2, 3}})


def test_fetch_compare_lines_refuses_non_sha_refs() -> None:
    """A ref that is not a sha never reaches URL construction."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)

    with patch("urllib.request.urlopen") as urlopen:
        lines = reporter.fetch_compare_lines(base="main", head="bbb222")

    assert_that(lines).is_none()
    assert_that(urlopen.called).is_false()


def test_fetch_compare_lines_returns_none_on_failure() -> None:
    """A failed comparison is unknown, not an empty diff."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)

    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        lines = reporter.fetch_compare_lines(base="aaa111", head="bbb222")

    assert_that(lines).is_none()
