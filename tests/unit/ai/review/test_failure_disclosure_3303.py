"""Posting regressions for failures without recoverable review state."""

from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.review.github import post_review_to_github
from lintro.ai.review.github_constants import GITHUB_COMMENT_HARD_LIMIT, STICKY_MARKER
from lintro.ai.review.github_errors import ERROR_ONLY_HEADLINE, format_error_comment
from lintro.ai.review.github_status import post_review_error_to_github
from lintro.ai.review.models.review_result import ReviewResult


@pytest.mark.parametrize("initial", ["empty", "board", "legacy", "unfinished"])
def test_repeated_failures_replace_disclosure(
    initial: str,
    sample_review_result: ReviewResult,
) -> None:
    """Only the latest failure remains visible and the board survives."""
    board = f"{STICKY_MARKER}\n## Findings\nP1: keep this finding\n"
    bodies = {
        "empty": "",
        "board": board,
        "legacy": format_error_comment(error=RuntimeError("stale cause")),
        "unfinished": board + "<!-- lintro-review-error-start -->stale cause",
    }
    live = [bodies[initial]]
    reporter = MagicMock()
    reporter.find_issue_comment.side_effect = lambda **_: (
        (7, live[-1]) if live[-1] else None
    )

    def save(*, comment_id: int = 7, body: str) -> int:
        """Capture the posted body."""
        live.append(body)
        return 200

    reporter.update_issue_comment_status.side_effect = save
    reporter.post_issue_comment.side_effect = lambda body: save(body=body) == 200
    for cause in ("stale cause", "latest cause"):
        assert_that(
            post_review_error_to_github(error=RuntimeError(cause), reporter=reporter),
        ).is_true()
    assert_that(live[-1].count(ERROR_ONLY_HEADLINE)).is_equal_to(1)
    assert_that(live[-1]).contains("latest cause").does_not_contain("stale cause")
    if initial in {"board", "unfinished"}:
        assert_that(live[-1]).starts_with(board)
    reporter.fetch_pr_diff_lines.return_value = {}
    result = sample_review_result
    assert_that(post_review_to_github(result=result, reporter=reporter)).is_true()
    assert_that(live[-1]).does_not_contain(
        "lintro-review-error-start",
        ERROR_ONLY_HEADLINE,
    )


@pytest.mark.parametrize("remaining", [0, 180])
def test_near_limit_board_is_preserved(remaining: int) -> None:
    """A full board stays intact and failure is disclosed on the pull request."""
    board = STICKY_MARKER + "x" * (
        GITHUB_COMMENT_HARD_LIMIT - len(STICKY_MARKER) - remaining
    )
    live = [board]
    separate: list[str] = []
    reporter = MagicMock()
    reporter.find_issue_comment.side_effect = [(7, board), None]

    def update(*, comment_id: int, body: str) -> int:
        """Capture the updated sticky."""
        live.append(body)
        return 200

    reporter.update_issue_comment_status.side_effect = update

    def post(body: str) -> bool:
        """Capture a separate failure comment."""
        separate.append(body)
        return True

    reporter.post_issue_comment.side_effect = post
    assert_that(
        post_review_error_to_github(error=RuntimeError("failure"), reporter=reporter),
    ).is_true()
    assert_that(live[-1]).starts_with(board)
    assert_that(len(live[-1])).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that("\n".join([live[-1], *separate])).contains(ERROR_ONLY_HEADLINE)
    if remaining == 0:
        assert_that(live[-1]).is_equal_to(board)
        assert_that(separate).is_length(1)
