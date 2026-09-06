"""Posting path for a round the convergence stop rule skipped (#2099)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.review.github import post_review_converged_to_github
from lintro.ai.review.github_constants import STATE_MARKER_PREFIX
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.review_state_codec import leftover_state_block
from lintro.ai.review.sticky import (
    advance_review_state,
    build_sticky_comment,
)

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
        request=StickyRequest(
            result=sample_review_result,
            head_sha="a" * 40,
            transport="cli",
            auth_mode="subscription",
        ),
    )


@pytest.fixture
def prior_state(sample_review_result: ReviewResult) -> ReviewState:
    """Artifact state persisted by a successful round."""
    return advance_review_state(
        request=StickyRequest(
            result=sample_review_result,
            head_sha="a" * 40,
            transport="cli",
            auth_mode="subscription",
        ),
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


def test_converged_stamp_leaves_the_sticky_alone_without_recoverable_state(
    prior_body: str,
) -> None:
    """With no artifact state and no decodable blob, nothing is overwritten.

    The primary sticky carries no hidden state of its own, so a fallback that
    rendered the empty-state page would erase the last board. The stamp
    refuses instead and reports False.
    """
    reporter = _reporter(prior_body=prior_body)

    posted = post_review_converged_to_github(
        decision=_DECISION,
        reporter=reporter,
        prior_state=ReviewState(),
    )

    assert_that(posted).is_false()
    reporter.update_issue_comment.assert_not_called()
    reporter.post_issue_comment.assert_not_called()


def test_converged_stamp_falls_back_to_the_stickys_own_state_blob(
    prior_state: ReviewState,
    prior_body: str,
) -> None:
    """With no artifact state, a decodable sticky blob still stamps in place.

    The artifact is not always available — a fresh runner, an expired upload,
    a local ``--post`` — but the sticky carries its own state. The stamp must
    recover the board from there rather than refusing (#2099 review).
    """
    reporter = _reporter(
        prior_body=prior_body + leftover_state_block(state=prior_state),
    )

    posted = post_review_converged_to_github(
        decision=_DECISION,
        reporter=reporter,
        prior_state=ReviewState(),
    )
    body = reporter.update_issue_comment.call_args.kwargs["body"]

    assert_that(posted).is_true()
    assert_that(body).contains("converged at round 2 (score 0.50 < threshold 3.00)")
    assert_that(prior_state.findings).is_not_empty()
    for record in prior_state.findings:
        assert_that(body).contains(record.title)


def test_converged_stamp_uses_the_reporter_pr_context_when_none_is_passed(
    prior_body: str,
    prior_state: ReviewState,
) -> None:
    """A successful stamp needs no explicit repo/pr_number.

    Mirrors ``test_posting_falls_back_to_the_reporter_pr_context`` (#1954):
    when the caller has no PR identity to hand, the reporter's own context is
    authoritative and posting still succeeds.

    Args:
        prior_body: Sticky body the reporter serves.
        prior_state: Artifact state persisted by the prior round.
    """
    reporter = _reporter(prior_body=prior_body)

    posted = post_review_converged_to_github(
        decision=_DECISION,
        reporter=reporter,
        prior_state=prior_state,
    )

    assert_that(posted).is_true()
    assert_that(
        reporter.update_issue_comment.call_args.kwargs["comment_id"],
    ).is_equal_to(
        9,
    )


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
