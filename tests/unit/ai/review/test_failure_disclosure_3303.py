"""Posting regressions for failures without recoverable review state."""

from dataclasses import replace
from urllib.error import URLError
from urllib.request import Request

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


@pytest.mark.parametrize("later", ["success", "error", "skip"])
def test_refused_notice_edit_and_legacy_duplicates_are_recoverable(
    later: str,
    sample_review_result: ReviewResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused edit cannot spawn B; old duplicates disappear after recovery."""
    request = StickyRequest(result=sample_review_result)
    board = build_sticky_comment(request=request)
    reporter = CommentReporter((board,))
    assert_that(
        post_review_error_to_github(error=RuntimeError("cause A"), reporter=reporter),
    ).is_true()
    notice_a = reporter.comments[2]
    reporter.delete_ok = False
    with monkeypatch.context() as patcher:
        patcher.setattr(reporter, "update_issue_comment_status", lambda **_: 403)
        assert_that(
            post_review_error_to_github(
                error=RuntimeError("cause B"),
                reporter=reporter,
            ),
        ).is_false()
    assert_that(reporter.comments).is_equal_to({1: board, 2: notice_a})
    # A duplicate left by an older version must also be cleaned up.
    reporter.comments[3] = FAILURE_NOTICE_MARKER + "\nlegacy cause B"
    reporter.delete_ok = True
    state = advance_review_state(request=request)
    if later == "success":
        posted = post_review_to_github(
            result=replace(sample_review_result, findings=()),
            reporter=reporter,
        )
        expected = "## 🔎 Lintro Review"
    elif later == "error":
        posted = post_review_error_to_github(
            error=RuntimeError("recovered cause"),
            reporter=reporter,
            prior_state=state,
        )
        expected = "recovered cause"
    else:
        posted = post_review_no_changes_to_github(reporter=reporter, prior_state=state)
        expected = "No reviewable changes, no review ran."
    assert_that(posted).is_true()
    assert_that(reporter.comments).is_length(1)
    assert_that(reporter.comments[1]).contains(expected).does_not_contain(
        "cause A",
        "legacy cause B",
    )


def test_notice_upsert_consolidates_duplicates(
    sample_review_result: ReviewResult,
) -> None:
    """A further failure retains one notice and updates it with the latest cause."""
    board = build_sticky_comment(request=StickyRequest(result=sample_review_result))
    reporter = CommentReporter(
        (board, FAILURE_NOTICE_MARKER + "\nold A", FAILURE_NOTICE_MARKER + "\nold B"),
    )
    assert_that(
        post_review_error_to_github(
            error=RuntimeError("latest cause"),
            reporter=reporter,
        ),
    ).is_true()
    assert_that(reporter.comments).is_length(2)
    assert_that(reporter.comments[1]).is_equal_to(board)
    assert_that(reporter.comments[2]).contains("latest cause").does_not_contain(
        "old A",
        "old B",
    )


@pytest.mark.parametrize("path", ["error", "skip"])
def test_failed_board_write_keeps_notice(
    path: str,
    sample_review_result: ReviewResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recoverable re-render must land before its failure notice is removed."""
    request = StickyRequest(result=sample_review_result)
    board = build_sticky_comment(request=request)
    reporter = CommentReporter((board, FAILURE_NOTICE_MARKER + "\nlatest failure"))
    before = reporter.comments.copy()
    monkeypatch.setattr(reporter, "update_issue_comment_status", lambda **_: 500)
    state = advance_review_state(request=request)
    if path == "error":
        posted = post_review_error_to_github(
            error=RuntimeError("new failure"),
            reporter=reporter,
            prior_state=state,
        )
    else:
        posted = post_review_no_changes_to_github(reporter=reporter, prior_state=state)
    assert_that(posted).is_false()
    assert_that(reporter.comments).is_equal_to(before)


def test_checkpoint_only_error_preserves_live_board(
    sample_review_result: ReviewResult,
) -> None:
    """Checkpoints are insufficient to replace a live board with an error page."""
    request = StickyRequest(result=sample_review_result)
    state = advance_review_state(request=request)
    state = replace(
        state,
        runs=tuple(
            replace(run, coverage=replace(run.coverage, checkpoint=True))
            for run in state.runs
        ),
    )
    before = state.to_dict()
    board = build_sticky_comment(request=request)
    reporter = CommentReporter((board,))
    assert_that(
        post_review_error_to_github(
            error=RuntimeError("checkpoint failure"),
            reporter=reporter,
            prior_state=state,
        ),
    ).is_true()
    assert_that(reporter.comments).is_length(2)
    assert_that(reporter.comments[1]).is_equal_to(board)
    assert_that(reporter.comments[2]).starts_with(FAILURE_NOTICE_MARKER).contains(
        "checkpoint failure",
    )
    assert_that(state.to_dict()).is_equal_to(before)


@pytest.mark.parametrize("notice", [False, True])
@pytest.mark.parametrize("path", ["success", "skip"])
@pytest.mark.parametrize("listing", ["valid", "malformed", "late-failure"])
def test_posting_reuses_one_paginated_listing(
    listing: str,
    notice: bool,
    path: str,
    sample_review_result: ReviewResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """451 comments cost five listing requests, including notice cleanup."""
    import json
    from io import BytesIO

    from lintro.ai.integrations.github_pr import GitHubPRReporter

    request = StickyRequest(result=sample_review_result)
    board = build_sticky_comment(request=request)
    bodies = [
        board,
        *["unrelated"] * 449,
        FAILURE_NOTICE_MARKER + "\nold failure" if notice else "last unrelated",
    ]
    reporter = CommentReporter(tuple(bodies))
    requests: list[str] = []

    def respond(req: Request, **_: object) -> BytesIO:
        """Serve ordered pages from the in-memory pull request."""
        requests.append(str(req.full_url))
        if listing == "late-failure" and len(requests) == 2:
            raise URLError("cleanup listing unavailable")
        start = (len(requests) - 1) * 100
        page: list[dict[str, object]] = [
            {"id": key, "body": body}
            for key, body in list(reporter.comments.items())[start : start + 100]
        ]
        if listing == "malformed":
            page.insert(0, {"id": "invalid", "body": None})
        return BytesIO(json.dumps(page).encode())

    monkeypatch.setattr(
        reporter,
        "lookup_issue_comments",
        GitHubPRReporter.lookup_issue_comments.__get__(reporter),
    )
    monkeypatch.setattr("urllib.request.urlopen", respond)
    if path == "success":
        posted = post_review_to_github(
            result=replace(sample_review_result, findings=()),
            reporter=reporter,
        )
    else:
        posted = post_review_no_changes_to_github(
            reporter=reporter,
            prior_state=advance_review_state(request=request),
        )
    assert_that(posted).is_true()
    assert_that(requests).is_length(2 if listing == "late-failure" else 5)
    assert_that(reporter.comments).is_length(
        450 if notice and listing != "late-failure" else 451,
    )
    assert_that(reporter.comments[1]).contains("## 🔎 Lintro Review").does_not_contain(
        "old failure",
    )


def test_inline_rejection_clears_notice_after_sticky_write(
    sample_review_result: ReviewResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected inline review cannot leave a false board-not-updated notice."""
    from lintro.ai.review import github
    from lintro.ai.review.models.inline_post_result import InlinePostResult

    board = STICKY_MARKER + "\nold board"
    reporter = CommentReporter((board, FAILURE_NOTICE_MARKER + "\nprevious failure"))
    finding = sample_review_result.findings[0]
    monkeypatch.setattr(
        reporter,
        "fetch_pr_diff_lines",
        lambda: {finding.file: {finding.line}},
    )
    monkeypatch.setattr(
        github,
        "_post_round_findings",
        lambda **_: InlinePostResult(ok=False, status=422),
    )
    monkeypatch.setattr(github, "run_thread_lifecycle", lambda **_: {})
    monkeypatch.setattr(
        reporter,
        "update_issue_comment",
        lambda **kwargs: reporter.update_issue_comment_status(**kwargs) == 200,
    )
    assert_that(
        post_review_to_github(result=sample_review_result, reporter=reporter),
    ).is_false()
    assert_that(reporter.comments).is_length(1)
    assert_that(reporter.comments[1]).contains(
        "## 🔎 Lintro Review",
        finding.title,
    ).does_not_contain("old board", "previous failure")


@pytest.mark.parametrize("failure", ["refused", "raises"])
def test_notice_cleanup_failure_cannot_fail_success_or_stop_remaining_cleanup(
    failure: str,
    sample_review_result: ReviewResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup is isolated per notice, and quoted markers never identify notices."""
    board = STICKY_MARKER + "\nBoard quoting " + FAILURE_NOTICE_MARKER
    notice = FAILURE_NOTICE_MARKER + "\nold failure"
    reporter = CommentReporter((board, notice, notice))
    delete = reporter.delete_issue_comment

    def remove(*, comment_id: int) -> bool:
        """Fail one deletion, leaving later notices removable."""
        if comment_id == 2:
            if failure == "raises":
                raise OSError("cleanup unavailable")
            return False
        return delete(comment_id=comment_id)

    monkeypatch.setattr(reporter, "delete_issue_comment", remove)
    assert_that(
        post_review_to_github(
            result=replace(sample_review_result, findings=()),
            reporter=reporter,
        ),
    ).is_true()
    assert_that(reporter.comments).is_length(2)
    assert_that(reporter.comments[1]).contains("## 🔎 Lintro Review")
    assert_that(reporter.comments[2]).is_equal_to(notice)
