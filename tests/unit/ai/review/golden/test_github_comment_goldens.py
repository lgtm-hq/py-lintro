"""Characterization goldens for the GitHub comment surfaces (issue #2303).

Both comments a review posts — the sticky mission-control board and the
failure surface — are snapshotted byte for byte here before #1974 moves the
size, state, and sanitisation invariants behind one contract module. The
comment is the product: a diff in these files is a change a reviewer sees, so
it has to be a decision rather than a side effect of a refactor.

Rewriting a golden is the same explicit opt-in the rest of the suite uses::

    LINTRO_UPDATE_GOLDENS=1 uv run pytest tests/unit/ai/review/golden
"""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review import github_errors
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.github import post_review_error_to_github
from lintro.ai.review.github_contract import (
    MAX_COMMENT_CHARS,
    STICKY_MARKER,
    CommentBudget,
    cap_body,
)
from lintro.ai.review.github_errors import format_error_comment
from lintro.ai.review.github_render import format_finding_comment
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky import (
    build_sticky_bodies,
    build_sticky_comment,
    render_state_sticky,
)
from tests.unit.ai.review.golden.github_comment_fixtures import (
    GOLDEN_HEAD_SHA,
    GOLDEN_PR_NUMBER,
    GOLDEN_REPO,
    golden_match,
    golden_prior_state,
    golden_review_result,
)
from tests.unit.ai.review.golden.golden_io import assert_golden


class _RecordingReporter:
    """Minimal stand-in for ``GitHubPRReporter`` that records the body posted.

    The error golden has to cover the body that actually reaches the API, not
    just the formatter's return value, so the posting path is driven end to
    end with the network replaced rather than the renderer called directly.
    """

    def __init__(self) -> None:
        """Start with no recorded body and a fixed PR context."""
        self.repo = GOLDEN_REPO
        self.pr_number = GOLDEN_PR_NUMBER
        self.posted: list[str] = []

    def is_available(self) -> bool:
        """Report the PR context as usable.

        Returns:
            bool: Always ``True``.
        """
        return True

    def find_issue_comment(self, *, marker: str) -> tuple[int, str] | None:
        """Report that no sticky comment exists yet.

        Args:
            marker: Marker the caller is looking for.

        Returns:
            tuple[int, str] | None: Always ``None``.
        """
        del marker
        return None

    def post_issue_comment(self, body: str) -> bool:
        """Record a created comment body.

        Args:
            body: Markdown body that would be posted.

        Returns:
            bool: Always ``True``.
        """
        self.posted.append(body)
        return True


def _sticky_kwargs() -> dict[str, Any]:
    """Return the fixed keyword arguments both sticky builders are called with.

    Returns:
        dict[str, Any]: Renderer inputs pinned by the fixtures module.
    """
    return {
        "result": golden_review_result(),
        "prior_state": golden_prior_state(),
        "head_sha": GOLDEN_HEAD_SHA,
        "transport": "api",
        "auth_mode": "api-key",
        "cost_basis": "estimated",
        "repo": GOLDEN_REPO,
        "pr_number": GOLDEN_PR_NUMBER,
    }


def test_sticky_comment_golden() -> None:
    """``build_sticky_comment`` renders the pinned board byte for byte."""
    body = build_sticky_comment(request=StickyRequest(**_sticky_kwargs()))

    assert_golden(name="github/sticky_comment.golden", actual=body)


def test_sticky_bodies_golden() -> None:
    """``build_sticky_bodies`` renders the same primary and no archive.

    The archive only splits out once history would push the primary past the
    soft limit, so the pinned two-round input must keep it ``None`` — a golden
    that quietly grew an archive comment would be a second comment on the PR.
    """
    primary, archive = build_sticky_bodies(request=StickyRequest(**_sticky_kwargs()))

    assert_golden(name="github/sticky_primary.golden", actual=primary)
    assert_that(archive).is_none()


def test_state_sticky_golden() -> None:
    """``render_state_sticky`` re-renders the board from state alone."""
    body = render_state_sticky(
        state=golden_prior_state(),
        banner="> ⚠️ **Round 3 could not complete** — pinned banner.",
        repo=GOLDEN_REPO,
        pr_number=GOLDEN_PR_NUMBER,
    )

    assert_golden(name="github/state_sticky.golden", actual=body)


def test_error_comment_first_round_golden() -> None:
    """The error-only surface is pinned for a first-round failure."""
    body = format_error_comment(
        error=RuntimeError("provider refused the request"),
        provider="anthropic",
        metadata=golden_review_result().metadata,
        prior_state=ReviewState(),
        repo=GOLDEN_REPO,
        pr_number=GOLDEN_PR_NUMBER,
    )

    assert_golden(name="github/error_comment_first_round.golden", actual=body)


def test_error_comment_over_a_prior_board_golden() -> None:
    """A failure over a recorded round is pinned as a banner, not a reset."""
    body = format_error_comment(
        error=RuntimeError("provider refused the request"),
        provider="anthropic",
        metadata=golden_review_result().metadata,
        prior_state=golden_prior_state(),
        repo=GOLDEN_REPO,
        pr_number=GOLDEN_PR_NUMBER,
    )

    assert_golden(name="github/error_comment_prior_board.golden", actual=body)


def test_posted_error_body_golden() -> None:
    """The body the posting path hands the API is pinned end to end."""
    reporter = _RecordingReporter()

    posted = post_review_error_to_github(
        error=RuntimeError("provider refused the request"),
        provider="anthropic",
        metadata=golden_review_result().metadata,
        reporter=reporter,  # type: ignore[arg-type]
    )

    assert_that(posted).is_true()
    assert_that(reporter.posted).is_length(1)
    assert_golden(name="github/posted_error_body.golden", actual=reporter.posted[0])


def test_oversized_error_body_is_capped_by_the_shared_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A monstrous provider message is truncated the way a sticky is.

    Before #2303 the error path sliced the string at the cap with no notice of
    its own, while the sticky renderer marked every drop it made. Both now go
    through :func:`cap_body`, so an error comment that had to lose text says
    so, and it still fits the budget GitHub's hard limit sits above.

    Every field this surface renders is itself length-capped, so the only way
    to reach the backstop is to widen one of those caps — here the provider
    cause limit, which is exactly the field a CLI transport can stuff a whole
    JSON payload into.
    """
    monkeypatch.setattr(github_errors, "ERROR_CAUSE_LIMIT", MAX_COMMENT_CHARS * 4)

    body = format_error_comment(
        error=RuntimeError("x" * (MAX_COMMENT_CHARS * 2)),
        provider="anthropic",
        prior_state=ReviewState(),
    )

    assert_that(len(body)).is_less_than_or_equal_to(MAX_COMMENT_CHARS)
    assert_that(body).contains("Comment truncated to fit GitHub's size limit")
    assert_that(body).starts_with(STICKY_MARKER)


def test_cap_body_reserves_room_for_a_trailer() -> None:
    """A reservation shrinks the body budget by exactly that many characters.

    This is the invariant the two posting paths used to hold separately: the
    body has to leave room for whatever gets concatenated after it.
    """
    budget = CommentBudget(max_chars=1_000, reserved=200)

    capped = cap_body(body="y" * 1_000, budget=budget)

    assert_that(budget.body_limit).is_equal_to(800)
    assert_that(len(capped)).is_less_than_or_equal_to(800)
    assert_that(capped).contains("Comment truncated to fit GitHub's size limit")


def test_review_body_first_round_golden() -> None:
    """The per-round review body is pinned for a first-round post.

    ``build_review_body`` is the third body-assembly path #2304 converges on
    the shared pipeline, so it needs the same byte-for-byte cover the sticky
    and error surfaces already have.
    """
    body = build_review_body(
        result=golden_review_result(),
        prior_state=ReviewState(),
        match=FindingMatchResult(),
        head_sha=GOLDEN_HEAD_SHA,
        transport="api",
        auth_mode="api-key",
        config_source="pyproject.toml",
        new_commits=None,
    )

    assert_golden(name="github/review_body_first_round.golden", actual=body)


def test_review_body_over_a_prior_board_golden() -> None:
    """The review body is pinned for a round carrying prior state."""
    body = build_review_body(
        result=golden_review_result(),
        prior_state=golden_prior_state(),
        match=golden_match(),
        head_sha=GOLDEN_HEAD_SHA,
        transport="api",
        auth_mode="api-key",
        config_source="pyproject.toml",
        new_commits=2,
    )

    assert_golden(name="github/review_body_prior_board.golden", actual=body)


def test_inline_finding_comment_golden() -> None:
    """The inline finding comment is pinned with its checklist link.

    The finding renderer moves modules in #2304's split of ``github_render``;
    a golden makes that move provable rather than assumed.
    """
    result = golden_review_result()
    body = format_finding_comment(
        finding=result.findings[0],
        checklist_display=ChecklistDisplay.LINKED,
        question_map={1: "Does an unknown status fail closed?"},
    )

    assert_golden(name="github/inline_finding_comment.golden", actual=body)
