"""Tests for how inline comments are anchored and scoped to a round (#1911)."""

from __future__ import annotations

import io
import json
import urllib.request
from dataclasses import replace
from http.client import HTTPMessage
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.models.github_api_response import GitHubApiResponse
from lintro.ai.review.github import post_review_to_github
from lintro.ai.review.github_constants import STICKY_MARKER
from lintro.ai.review.github_sticky import advance_review_state
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.suggested_change import SuggestedChange
from lintro.ai.review.review_state_codec import legacy_state_block

_TEST_TOKEN = "ghp_test_fixture_token"  # nosec B105 — fake test fixture token


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
    reporter.api_response.return_value = GitHubApiResponse(status=200)
    reporter.api_base = "https://api.github.com"
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


def _prior_sticky(*, result: ReviewResult, head_sha: str) -> str:
    """Render a leftover v2 sticky so posting can migrate prior state."""
    state = advance_review_state(result=result, head_sha=head_sha)
    return STICKY_MARKER + legacy_state_block(state=state)


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
    payload = reporter.api_response.call_args.args[2]
    # Guard the positional index: if the production call ever stops passing the
    # payload third, this fails naming the cause instead of a bare KeyError.
    assert_that(payload).is_instance_of(dict)
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
        _prior_sticky(result=result, head_sha="aaa111"),
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
        _prior_sticky(result=result, head_sha="aaa111"),
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
        _prior_sticky(result=prior, head_sha="aaa111"),
    )
    reporter.fetch_compare_lines.return_value = {"src/main.py": {9, 10, 11}}
    # Same location, different finding — round 2 sees it for the first time.
    first = replace(prior.findings[0], title="A different defect entirely")
    result = replace(prior, findings=(first, *prior.findings[1:]))

    post_review_to_github(result=result, reporter=reporter)

    assert_that(_inline_comments(reporter=reporter)[0]["body"]).contains(
        "```suggestion",
    )


def test_prior_round_without_a_recorded_sha_falls_back_to_mode_b(
    sample_review_result: ReviewResult,
) -> None:
    """A sticky from a version that never stored a sha leaves the round unknown."""
    reporter = _reporter()
    result = _with_change(
        result=sample_review_result,
        change=SuggestedChange(start_line=10, end_line=10, replacement="ok"),
    )
    # A run entry exists, so this is not round 1, but it carries no sha — there
    # is nothing to compare against and no suggestion may claim to be on this
    # round's diff.
    reporter.find_issue_comment.return_value = (
        42,
        _prior_sticky(result=result, head_sha=""),
    )

    post_review_to_github(result=result, reporter=reporter)

    assert_that(reporter.fetch_compare_lines.called).is_false()
    body = _inline_comments(reporter=reporter)[0]["body"]
    assert_that(body).does_not_contain("```suggestion")
    assert_that(body).contains("**Fix:**")


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


def test_fetch_compare_lines_merges_duplicate_filenames() -> None:
    """A filename listed twice keeps every line it changed."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)
    payload = {
        "files": [
            {"filename": "src/main.py", "patch": "@@ -1 +1 @@\n+one"},
            {"filename": "src/main.py", "patch": "@@ -9 +9 @@\n+nine"},
        ],
    }

    with patch("urllib.request.urlopen", return_value=_reader(payload)):
        lines = reporter.fetch_compare_lines(base="aaa111", head="bbb222")

    assert_that(lines).is_equal_to({"src/main.py": {1, 9}})


def test_fetch_compare_lines_skips_non_mapping_entries() -> None:
    """A malformed files array degrades rather than raising past the handler."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)
    payload = {
        "files": [
            "not-a-file",
            {"filename": "src/main.py", "patch": "@@ -1 +1 @@\n+one"},
        ],
    }

    with patch("urllib.request.urlopen", return_value=_reader(payload)):
        lines = reporter.fetch_compare_lines(base="aaa111", head="bbb222")

    assert_that(lines).is_equal_to({"src/main.py": {1}})


def test_fetch_compare_lines_skips_malformed_filename_and_patch_values() -> None:
    """A wrongly-typed field is skipped, not raised past the caller's handler."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)
    payload = {
        "files": [
            {"filename": ["src/main.py"], "patch": "@@ -1 +1 @@\n+one"},
            {"filename": "src/other.py", "patch": {"not": "a string"}},
            {"filename": "src/main.py", "patch": "@@ -1 +1 @@\n+one"},
        ],
    }

    with patch("urllib.request.urlopen", return_value=_reader(payload)):
        lines = reporter.fetch_compare_lines(base="aaa111", head="bbb222")

    assert_that(lines).is_equal_to({"src/main.py": {1}})


def test_fetch_compare_lines_rejects_a_sha_with_a_trailing_newline() -> None:
    """A trailing newline must not reach URL construction."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)

    with patch("urllib.request.urlopen") as urlopen:
        lines = reporter.fetch_compare_lines(base="aaa111\n", head="bbb222")

    assert_that(lines).is_none()
    assert_that(urlopen.called).is_false()


def test_fetch_compare_lines_issues_exactly_one_request() -> None:
    """The compare endpoint paginates commits, not files — do not walk pages."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)
    payload = {
        "files": [
            {"filename": f"src/f{index}.py", "patch": "@@ -1 +1 @@\n+one"}
            for index in range(100)
        ],
    }

    with patch("urllib.request.urlopen", return_value=_reader(payload)) as urlopen:
        lines = reporter.fetch_compare_lines(base="aaa111", head="bbb222")

    assert_that(urlopen.call_count).is_equal_to(1)
    assert_that(lines).is_length(100)
    requested = urlopen.call_args.args[0].full_url
    assert_that(requested).does_not_contain("page=")


def test_fetch_compare_lines_returns_none_without_a_files_array() -> None:
    """A response that names no files is unknown, not an empty diff."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)

    with patch("urllib.request.urlopen", return_value=_reader({"status": "identical"})):
        lines = reporter.fetch_compare_lines(base="aaa111", head="bbb222")

    assert_that(lines).is_none()


def test_api_requests_never_replay_the_token_to_a_redirect_target() -> None:
    """Urllib copies ordinary headers across redirects; the token must not go."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)

    with patch(
        "urllib.request.urlopen",
        return_value=_reader({"files": []}),
    ) as urlopen:
        reporter.fetch_compare_lines(base="aaa111", head="bbb222")

    request = urlopen.call_args.args[0]
    # ``header_items()`` merges both maps, so the split is only visible in the
    # underlying dicts: ordinary headers ride along on a redirect, unredirected
    # ones do not.
    assert_that(request.headers).does_not_contain_key("Authorization")
    assert_that(request.unredirected_hdrs).contains_key("Authorization")

    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        request,
        io.BytesIO(),
        302,
        "Found",
        HTTPMessage(),
        "http://evil.example/steal",
    )
    if redirected is None:  # pragma: no cover - urllib always redirects a 302
        pytest.fail("expected urllib to build a redirected request")
    assert_that(dict(redirected.header_items())).does_not_contain_key("Authorization")


def test_fetch_compare_lines_returns_none_on_failure() -> None:
    """A failed comparison is unknown, not an empty diff."""
    reporter = GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)

    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        lines = reporter.fetch_compare_lines(base="aaa111", head="bbb222")

    assert_that(lines).is_none()
