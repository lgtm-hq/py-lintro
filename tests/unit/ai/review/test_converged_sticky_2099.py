"""Posting path for a round the convergence stop rule skipped (#2099)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.review.github import post_review_converged_to_github
from lintro.ai.review.github_constants import STATE_MARKER_PREFIX
from lintro.ai.review.github_sticky import (
    advance_review_state,
    build_sticky_comment,
)
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState

# One real round persisted, so the skipped round is round 2.
_DECISION = ConvergenceDecision(
    converged=True,
    round_number=2,
    score=0.5,
    threshold=3.0,
    stable_rounds=1,
    trajectory=(0.5,),
)


@pytest.fixture
def prior_body(sample_review_result: ReviewResult) -> str:
    """Render a successful round-1 sticky the skipped round stamps onto."""
    return build_sticky_comment(
        result=sample_review_result,
        head_sha="a" * 40,
        transport="cli",
        auth_mode="subscription",
    )


@pytest.fixture
def prior_state(sample_review_result: ReviewResult) -> ReviewState:
    """Artifact state persisted by a successful round."""
    return advance_review_state(
        result=sample_review_result,
        head_sha="a" * 40,
        transport="cli",
        auth_mode="subscription",
    )


def _reporter(*, prior_body: str, available: bool = True) -> MagicMock:
    """Build a mock reporter serving ``prior_body`` as the existing sticky.

    Args:
        prior_body: Sticky body the reporter reports as already posted.
        available: Whether the reporter reports a usable PR context.

    Returns:
        The configured mock.
    """
    reporter = MagicMock()
    reporter.is_available.return_value = available
    reporter.find_issue_comment.return_value = (9, prior_body)
    reporter.update_issue_comment.return_value = True
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


def test_converged_stamp_updates_the_sticky_in_place(
    prior_body: str,
    prior_state: ReviewState,
) -> None:
    """The stamp edits the existing sticky and keeps the last good board."""
    reporter = _reporter(prior_body=prior_body)

    posted = post_review_converged_to_github(
        decision=_DECISION,
        repo="owner/name",
        pr_number=7,
        reporter=reporter,
        prior_state=prior_state,
    )
    kwargs = reporter.update_issue_comment.call_args.kwargs
    body = kwargs["body"]

    assert_that(posted).is_true()
    assert_that(kwargs["comment_id"]).is_equal_to(9)
    assert_that(body).contains("🔁 **Converged**")
    assert_that(body).contains("converged at round 2 (score 0.50 < threshold 3.00)")
    assert_that(body).contains("### Findings ·")
    # The board is the last real round's, not a blank one: every tracked
    # finding title from persisted state is still on it.
    for record in prior_state.findings:
        assert_that(body).contains(record.title)
    # Like a failed round, a skipped round writes no state of its own.
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)
    assert_that(prior_state.next_round).is_equal_to(2)


def test_converged_stamp_falls_back_to_the_sticky_state(
    prior_body: str,
) -> None:
    """With no artifact state, the stamp still lands on the existing sticky.

    The primary sticky carries no hidden state of its own (state lives in the
    CI artifact or local ledger), so the fallback renders the empty-state
    board under the banner rather than inventing history.
    """
    reporter = _reporter(prior_body=prior_body)

    posted = post_review_converged_to_github(
        decision=_DECISION,
        reporter=reporter,
        prior_state=ReviewState(),
    )
    kwargs = reporter.update_issue_comment.call_args.kwargs

    assert_that(posted).is_true()
    assert_that(kwargs["comment_id"]).is_equal_to(9)
    assert_that(kwargs["body"]).contains("🔁 **Converged**")


def test_converged_stamp_skips_when_no_pr_context(prior_body: str) -> None:
    """Without a PR context nothing is posted and the call reports False."""
    reporter = _reporter(prior_body=prior_body, available=False)

    posted = post_review_converged_to_github(
        decision=_DECISION,
        reporter=reporter,
    )

    assert_that(posted).is_false()
    reporter.update_issue_comment.assert_not_called()
    reporter.post_issue_comment.assert_not_called()
