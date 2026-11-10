"""Posting regressions for failures without recoverable review state."""

from dataclasses import replace

import pytest
from assertpy import assert_that

from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.github import post_review_to_github
from lintro.ai.review.github_constants import (
    FAILURE_NOTICE_MARKER,
    GITHUB_COMMENT_HARD_LIMIT,
    STICKY_MARKER,
)
from lintro.ai.review.github_errors import format_error_comment
from lintro.ai.review.github_status import (
    post_review_error_to_github,
    post_review_no_changes_to_github,
)
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky import advance_review_state, build_sticky_comment
from tests.unit.ai.review.comment_reporter import CommentReporter


@pytest.mark.parametrize("initial", ["absent", "error", "error-metadata"])
def test_repeated_first_round_failures(
    initial: str,
    sample_review_result: ReviewResult,
) -> None:
    """First-round failures update one complete error page with the latest cause."""
    reporter = CommentReporter(
        (
            ()
            if initial == "absent"
            else (
                format_error_comment(
                    error=RuntimeError("old cause"),
                    metadata=(
                        sample_review_result.metadata
                        if initial == "error-metadata"
                        else None
                    ),
                ),
            )
        ),
    )
    for cause in ("first cause", "latest cause"):
        assert_that(
            post_review_error_to_github(error=RuntimeError(cause), reporter=reporter),
        ).is_true()
    assert_that(list(reporter.comments.values())).is_equal_to(
        [format_error_comment(error=RuntimeError("latest cause"))],
    )
    assert_that(reporter.find_issue_comment(marker=FAILURE_NOTICE_MARKER)).is_none()


@pytest.mark.parametrize("board_kind", ["current", "hostile", "full", "uncertain"])
def test_unrecoverable_board_is_never_edited(
    board_kind: str,
    sample_review_result: ReviewResult,
) -> None:
    """Repeated failures preserve every byte of a board and update one notice."""
    result = replace(
        sample_review_result,
        metadata=replace(sample_review_result.metadata, head_ref="review-head"),
    )
    if board_kind == "hostile":
        finding = replace(
            result.findings[0],
            title="<!-- lintro-review-error-start --> <!-- lintro-review-error-end --> "
            + FAILURE_NOTICE_MARKER
            + " ❌ **Review could not complete**",
        )
        result = replace(result, findings=(finding,))
    board = build_sticky_comment(request=StickyRequest(result=result))
    if board_kind == "full":
        board += "x" * (GITHUB_COMMENT_HARD_LIMIT - len(board))
    if board_kind == "uncertain":
        board = (
            format_error_comment(error=RuntimeError("old cause"))
            + "\n\n### Findings\nKeep this finding"
        )
    reporter = CommentReporter((board,))
    for cause in ("first cause", "second cause", "latest cause"):
        assert_that(
            post_review_error_to_github(
                error=ReviewContextError(cause, code="gh-command-failed"),
                provider="anthropic",
                metadata=result.metadata,
                reporter=reporter,
            ),
        ).is_true()
        assert_that(reporter.comments[1]).is_equal_to(board)
        assert_that(reporter.comments).is_length(2)
    notice = reporter.find_issue_comment(marker=FAILURE_NOTICE_MARKER)
    notice_body = reporter.comments[2]
    assert_that(notice).is_equal_to((2, notice_body))
    assert_that(notice_body).contains(
        "latest cause",
        "Latest review",
        "findings board in the sticky comment was not updated",
    ).does_not_contain(
        "first cause",
        "second cause",
        "`anthropic`",
        "provider reported",
    )
    if result.metadata.head_ref:
        assert_that(notice_body).contains(result.metadata.head_ref)
    assert_that(reporter.find_issue_comment(marker=STICKY_MARKER)).is_equal_to(
        (1, board),
    )
    isolated = CommentReporter((notice_body,))
    assert_that(isolated.find_issue_comment(marker=STICKY_MARKER)).is_none()


@pytest.mark.parametrize(
    "next_run",
    ["success", "skip", "recoverable-skip", "recoverable-error"],
)
def test_later_run_removes_notice(
    next_run: str,
    sample_review_result: ReviewResult,
) -> None:
    """Success, a skip, and a recoverable error clear the separate notice."""
    request = StickyRequest(result=sample_review_result)
    board = build_sticky_comment(request=request)
    state = advance_review_state(request=request)
    before = state.to_dict()
    reporter = CommentReporter((board,))
    post_review_error_to_github(
        error=RuntimeError("first cause " + STICKY_MARKER),
        reporter=reporter,
    )
    assert_that(reporter.comments).is_length(2)
    if next_run == "success":
        assert_that(
            post_review_to_github(
                result=replace(sample_review_result, findings=()),
                reporter=reporter,
            ),
        ).is_true()
        assert_that(reporter.comments[1]).contains(
            "## 🔎 Lintro Review",
        ).does_not_contain("could not complete")
    elif next_run in {"skip", "recoverable-skip"}:
        posted = post_review_no_changes_to_github(
            reporter=reporter,
            prior_state=state if next_run == "recoverable-skip" else None,
        )
        assert_that(posted).is_equal_to(next_run == "recoverable-skip")
        if next_run == "skip":
            assert_that(reporter.comments[1]).is_equal_to(board)
        else:
            assert_that(reporter.comments[1]).contains(
                "No reviewable changes, no review ran.",
            )
    else:
        error = RuntimeError("latest cause")
        assert_that(
            post_review_error_to_github(
                error=error,
                reporter=reporter,
                prior_state=state,
            ),
        ).is_true()
        assert_that(reporter.comments[1]).is_equal_to(
            format_error_comment(
                error=error,
                prior_state=state,
                repo=reporter.repo,
                pr_number=reporter.pr_number,
            ),
        )
    assert_that(reporter.comments).is_length(1)
    assert_that(reporter.find_issue_comment(marker=FAILURE_NOTICE_MARKER)).is_none()
    assert_that(state.to_dict()).is_equal_to(before)


def test_lookup_unknown_leaves_all_comments_untouched(
    sample_review_result: ReviewResult,
) -> None:
    """A failed listing cannot create a second sticky or change existing comments."""
    board = build_sticky_comment(request=StickyRequest(result=sample_review_result))
    reporter = CommentReporter((board, FAILURE_NOTICE_MARKER + "\nold notice"))
    before = reporter.comments.copy()
    reporter.lookup_unknown = True
    assert_that(
        post_review_error_to_github(
            error=RuntimeError("latest cause"),
            reporter=reporter,
        ),
    ).is_false()
    assert_that(reporter.comments).is_equal_to(before)


def test_failed_notice_deletion_does_not_fail_skip() -> None:
    """A refused cleanup leaves the old notice without changing skip behavior."""
    reporter = CommentReporter((FAILURE_NOTICE_MARKER + "\nold notice",))
    reporter.delete_ok = False
    assert_that(post_review_no_changes_to_github(reporter=reporter)).is_false()
    assert_that(list(reporter.comments.values())).is_equal_to(
        [FAILURE_NOTICE_MARKER + "\nold notice"],
    )


def test_notice_cannot_be_adopted_as_sticky() -> None:
    """Untrusted failure text cannot put the sticky marker into a notice."""
    board = STICKY_MARKER + "\nFindings board"
    reporter = CommentReporter((board,))
    post_review_error_to_github(
        error=RuntimeError("cause quotes " + STICKY_MARKER),
        reporter=reporter,
    )
    notice = reporter.comments[2]
    isolated = CommentReporter((notice,))
    assert_that(isolated.find_issue_comment(marker=STICKY_MARKER)).is_none()
    assert_that(isolated.find_issue_comment(marker=FAILURE_NOTICE_MARKER)).is_equal_to(
        (1, notice),
    )
    assert_that(reporter.comments[1]).is_equal_to(board)
