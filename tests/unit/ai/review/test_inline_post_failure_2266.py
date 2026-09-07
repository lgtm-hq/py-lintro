"""Inline-post failures are reported as what GitHub actually answered (#2266).

The sticky comment used to blame every rejected inline batch partly on
findings that anchor outside the diff, because the post helper reported only a
boolean. A 403 secondary rate limit was then rendered as a line-mapping
problem and the CI summary still claimed the P1 findings had been posted.
These tests pin the honest wording on each surface.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.integrations.github_pr import _error_message
from lintro.ai.models.github_api_response import GitHubApiResponse
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.inline_post_failure_kind import InlinePostFailureKind
from lintro.ai.review.github import post_review_to_github
from lintro.ai.review.github_inline import post_inline_findings
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.inline_post_request import InlinePostRequest
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.output import (
    INLINE_POST_FAILURE_KEY,
    render_inline_post_failure_json,
)
from lintro.ai.review.sticky import build_sticky_comment

#: Message GitHub returns when it throttles content creation on a token.
_RATE_LIMIT_MESSAGE = (
    "You have exceeded a secondary rate limit and have been temporarily "
    "blocked from content creation."
)

#: Message a 422 returns when a comment anchors outside the PR's diff.
_LINE_MESSAGE = "Validation Failed line: must be part of the diff"


@pytest.fixture
def warnings_log() -> Iterator[list[str]]:
    """Capture warning-level loguru records emitted during a test.

    Yields:
        list[str]: The list the sink appends formatted records to.
    """
    records: list[str] = []
    handler_id = logger.add(records.append, level="WARNING")
    try:
        yield records
    finally:
        logger.remove(handler_id)


def _reporter(*, response: GitHubApiResponse) -> MagicMock:
    """Build a reporter whose whole result is diff-mappable.

    Every sample finding anchors to a diff line, so anything folded into the
    sticky got there because GitHub refused the review — never because a
    finding had no line to attach to.

    Args:
        response: Answer the inline review POST receives.

    Returns:
        The configured mock reporter.
    """
    reporter = MagicMock()
    reporter.is_available.return_value = True
    reporter.find_issue_comment.return_value = (77, "")
    diff_lines = {"src/main.py": {10}, "tests/test_main.py": {5}}
    reporter.fetch_pr_diff_lines.return_value = diff_lines
    reporter.fetch_compare_lines.return_value = diff_lines
    reporter.fetch_pr_commit_shas.return_value = []
    sticky_bodies: list[str] = []
    reporter.sticky_bodies = sticky_bodies

    def _post_issue_comment(body: Any, **_kwargs: Any) -> bool:
        """Record a newly posted sticky body.

        Args:
            body: Sticky comment body the production code posted.
            **_kwargs: Ignored posting extras.

        Returns:
            bool: Always ``True``, the success result GitHub would return.
        """
        sticky_bodies.append(str(body))
        return True

    def _update_issue_comment(**kwargs: Any) -> bool:
        """Record an edited sticky body.

        Args:
            **kwargs: Update arguments, of which ``body`` is recorded.

        Returns:
            bool: Always ``True``, the success result GitHub would return.
        """
        sticky_bodies.append(str(kwargs["body"]))
        return True

    reporter.post_issue_comment.side_effect = _post_issue_comment
    reporter.update_issue_comment.side_effect = _update_issue_comment
    reporter.delete_issue_comment.return_value = True
    reporter.api_response.return_value = response
    reporter.api_base = "https://api.github.com"
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


def _degraded_sticky(*, result: ReviewResult, response: GitHubApiResponse) -> str:
    """Post a review whose inline batch GitHub refuses and return the sticky.

    Args:
        result: Review result to post.
        response: Answer the inline review POST receives.

    Returns:
        The final sticky body written to the PR.
    """
    reporter = _reporter(response=response)
    posted = post_review_to_github(result=result, reporter=reporter)

    assert_that(posted).is_false()
    return str(reporter.update_issue_comment.call_args.kwargs["body"])


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (403, _RATE_LIMIT_MESSAGE, InlinePostFailureKind.RATE_LIMITED),
        (429, _RATE_LIMIT_MESSAGE, InlinePostFailureKind.RATE_LIMITED),
        (403, "API rate limit exceeded for user", InlinePostFailureKind.RATE_LIMITED),
        (429, "Too Many Requests", InlinePostFailureKind.RATE_LIMITED),
        (422, _LINE_MESSAGE, InlinePostFailureKind.LINE_MAPPING),
        (
            403,
            "Resource not accessible by integration",
            InlinePostFailureKind.PERMISSION,
        ),
        (401, "Bad credentials", InlinePostFailureKind.PERMISSION),
        (500, "Server Error", InlinePostFailureKind.OTHER),
        (422, "Validation Failed", InlinePostFailureKind.OTHER),
        (422, "pipeline disposition rejected", InlinePostFailureKind.OTHER),
        (None, "", InlinePostFailureKind.OTHER),
    ],
    ids=[
        "kind=rate_limited",
        "kind=rate_limited_429",
        "kind=rate_limited_primary",
        "kind=rate_limited_429_terse",
        "kind=line_mapping",
        "kind=permission_forbidden",
        "kind=permission_unauthorized",
        "kind=other_server_error",
        "kind=other_unspecific_422",
        "kind=other_422_substring_only",
        "kind=other_no_answer",
    ],
)
def test_failure_kind_is_derived_from_the_status_and_message(
    status: int | None,
    message: str,
    expected: InlinePostFailureKind,
) -> None:
    """Each documented status/message pair maps to its classified kind."""
    kind = InlinePostFailureKind.from_response(status=status, message=message)

    assert_that(kind).is_equal_to(expected)


def test_secondary_rate_limit_is_named_instead_of_guessed_line_mapping(
    sample_review_result: ReviewResult,
) -> None:
    """A throttled token is reported as a rate limit, not a diff-mapping miss."""
    body = _degraded_sticky(
        result=sample_review_result,
        response=GitHubApiResponse(status=403, message=_RATE_LIMIT_MESSAGE),
    )

    assert_that(body).contains("GitHub rate limit (HTTP 403)")
    assert_that(body).does_not_contain("map to no line in this PR's diff")


def test_line_validation_error_keeps_the_diff_mapping_wording(
    sample_review_result: ReviewResult,
) -> None:
    """A 422 about a line is the one rejection that may blame the diff."""
    body = _degraded_sticky(
        result=sample_review_result,
        response=GitHubApiResponse(status=422, message=_LINE_MESSAGE),
    )

    assert_that(body).contains("map to no line in this PR's diff (HTTP 422)")
    assert_that(body).does_not_contain("secondary rate limit")


def test_permission_rejection_names_the_token(
    sample_review_result: ReviewResult,
) -> None:
    """A 403 without rate-limit wording is a permission problem."""
    body = _degraded_sticky(
        result=sample_review_result,
        response=GitHubApiResponse(
            status=403,
            message="Resource not accessible by integration",
        ),
    )

    assert_that(body).contains("not permitted to post reviews on this PR (HTTP 403)")


def test_line_mapping_rejection_states_the_cause_once(
    sample_review_result: ReviewResult,
) -> None:
    """A 422 alongside unmappable findings does not say the same thing twice."""
    reporter = _reporter(
        response=GitHubApiResponse(status=422, message=_LINE_MESSAGE),
    )
    # Only one of the two sample findings anchors to a diff line, so the round
    # carries a rejected finding and an unmappable one at the same time.
    reporter.fetch_pr_diff_lines.return_value = {"src/main.py": {10}}
    reporter.fetch_compare_lines.return_value = {"src/main.py": {10}}

    posted = post_review_to_github(result=sample_review_result, reporter=reporter)

    assert_that(posted).is_false()
    assert_that(reporter.sticky_bodies).is_not_empty()
    body = reporter.sticky_bodies[-1]
    row = next(line for line in body.splitlines() if "could not be posted" in line)

    assert_that(row.count("map to no line in this PR's diff")).is_equal_to(1)


def test_accepted_inline_batch_leaves_no_degraded_row(
    sample_review_result: ReviewResult,
) -> None:
    """A successful round says nothing about unpostable findings."""
    reporter = _reporter(response=GitHubApiResponse(status=200))

    posted = post_review_to_github(result=sample_review_result, reporter=reporter)

    assert_that(posted).is_true()
    body = str(reporter.update_issue_comment.call_args.kwargs["body"])
    assert_that(body).does_not_contain("could not be posted")


def test_rate_limited_round_logs_the_machine_readable_envelope(
    sample_review_result: ReviewResult,
    warnings_log: list[str],
) -> None:
    """The CI classifier's envelope names the kind and the status."""
    _degraded_sticky(
        result=sample_review_result,
        response=GitHubApiResponse(status=403, message=_RATE_LIMIT_MESSAGE),
    )

    logged = [line for line in warnings_log if INLINE_POST_FAILURE_KEY in line]
    assert_that(logged).is_not_empty()
    start = logged[0].index("{")
    payload = json.loads(logged[0][start : logged[0].rindex("}") + 1])
    failure = payload[INLINE_POST_FAILURE_KEY]

    assert_that(failure["kind"]).is_equal_to("rate_limited")
    assert_that(failure["status"]).is_equal_to(403)
    assert_that(failure["count"]).is_equal_to(len(sample_review_result.findings))


def test_envelope_omits_the_status_when_nothing_was_submitted() -> None:
    """Findings that were never posted claim no HTTP answer of their own."""
    payload = json.loads(
        render_inline_post_failure_json(
            failure=InlinePostFailure(reason="no line", findings=()),
        ),
    )

    assert_that(payload[INLINE_POST_FAILURE_KEY]).does_not_contain_key("status")
    assert_that(payload[INLINE_POST_FAILURE_KEY]["kind"]).is_equal_to("line_mapping")


def test_sticky_row_reports_the_kind_supplied_by_the_caller(
    sample_review_result: ReviewResult,
) -> None:
    """The public sticky builder renders whatever cause it is handed."""
    body = build_sticky_comment(
        request=StickyRequest(
            result=sample_review_result,
            inline_failure=InlinePostFailure(
                reason="GitHub rate limit (HTTP 403)",
                findings=sample_review_result.findings,
                kind=InlinePostFailureKind.RATE_LIMITED,
                status=403,
            ),
        ),
    )

    assert_that(body).contains("GitHub rate limit (HTTP 403)")


def test_inline_post_result_carries_the_status_and_attempted_ids(
    sample_review_result: ReviewResult,
) -> None:
    """The post helper reports the answer, not merely that it failed."""
    reporter = _reporter(
        response=GitHubApiResponse(status=403, message=_RATE_LIMIT_MESSAGE),
    )

    outcome = post_inline_findings(
        reporter=reporter,
        request=InlinePostRequest(
            findings=list(sample_review_result.findings),
            checklist_display=ChecklistDisplay.OFF,
            finding_keys=("key-a", "key-b"),
        ),
    )

    assert_that(outcome.ok).is_false()
    assert_that(outcome.status).is_equal_to(403)
    assert_that(outcome.message).contains("secondary rate limit")
    assert_that(outcome.attempted_ids).is_equal_to(("key-a", "key-b"))


@pytest.mark.parametrize("status", [403, 429])
def test_secondary_rate_limit_is_classified_on_both_statuses(status: int) -> None:
    """GitHub uses 403 and 429 for the same throttle; both read as rate limited.

    Args:
        status: HTTP status under test.
    """
    from lintro.ai.review.enums.inline_post_failure_kind import InlinePostFailureKind

    kind = InlinePostFailureKind.from_response(
        status=status,
        message="You have exceeded a secondary rate limit and have been "
        "temporarily blocked from content creation.",
    )

    assert_that(kind).is_equal_to(InlinePostFailureKind.RATE_LIMITED)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            422,
            json.dumps(
                {
                    "message": "Validation Failed",
                    "errors": [
                        {
                            "resource": "PullRequestReviewComment",
                            "code": "custom",
                            "field": "line",
                            "message": "line must be part of the diff",
                        },
                    ],
                },
            ),
            InlinePostFailureKind.LINE_MAPPING,
        ),
        (
            422,
            json.dumps(
                {
                    "message": "Validation Failed",
                    "errors": [
                        {
                            "resource": "PullRequestReviewComment",
                            "code": "custom",
                            "field": "position",
                            "message": "must be within the diff",
                        },
                    ],
                },
            ),
            InlinePostFailureKind.LINE_MAPPING,
        ),
        (
            403,
            json.dumps({"message": _RATE_LIMIT_MESSAGE}),
            InlinePostFailureKind.RATE_LIMITED,
        ),
        (
            422,
            json.dumps({"message": "Validation Failed", "errors": ["pipeline"]}),
            InlinePostFailureKind.OTHER,
        ),
    ],
    ids=[
        "folded_line_field",
        "folded_position_field",
        "folded_secondary_rate_limit",
        "folded_unrelated_422",
    ],
)
def test_real_github_bodies_classify_after_folding(
    status: int,
    body: str,
    expected: InlinePostFailureKind,
) -> None:
    """A raw GitHub error body reaches the right kind once errors[] is folded in.

    A 422 says only "Validation Failed" until its per-field entries are merged,
    so this wires ``_error_message`` to ``from_response`` the way the reporter
    does at runtime.
    """
    message = _error_message(body=body)

    kind = InlinePostFailureKind.from_response(status=status, message=message)

    assert_that(kind).is_equal_to(expected)
