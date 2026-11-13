"""The sticky footer claims inline comments only when some were posted (#3410)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.models.github_api_response import GitHubApiResponse
from lintro.ai.review.github import post_review_to_github
from lintro.ai.review.github_constants import STICKY_FOOTER
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky import build_sticky_comment, render_state_sticky
from lintro.ai.review.sticky.footer import inline_split, sticky_footer
from tests.unit.ai.review.comment_reporter import use_combined_comment_lookup

_HOW_TO_READ = (
    "[how to read this report](https://github.com/lgtm-hq/py-lintro/blob/main/"
    "docs/ai-review-report.md)"
)
_NEUTRAL = f"<sub>🤖 lintro review · {_HOW_TO_READ}</sub>"
_NONE_INLINE = (
    "<sub>🤖 lintro review · findings are listed above, none commented inline · "
    f"{_HOW_TO_READ}</sub>"
)


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a review finding that would open an inline thread by default.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The constructed finding.
    """
    fields: dict[str, Any] = {
        "severity": Severity.P1,
        "category": "logic-bug",
        "file": "src/app.py",
        "line": 12,
        "title": "Branch never taken",
        "description": "The guard is inverted.",
        "cause": "Negated twice.",
        "fix": "Drop one negation.",
        "confidence": "high",
    }
    fields.update(overrides)
    return ReviewFinding(**fields)


_BLOCKER = _finding()
_MAJOR = _finding(severity=Severity.P2, title="Leaked handle", line=30)
_NIT = _finding(severity=Severity.P3, title="Stale docstring", line=5)
_NOTE = _finding(
    severity=Severity.P2,
    title="Maybe racy",
    confidence="low",
    posted_inline=False,
)


def _footer(body: str) -> str:
    """Return the last line of a rendered sticky body.

    Args:
        body: Rendered sticky comment.

    Returns:
        The footer line.
    """
    return body.rstrip().splitlines()[-1]


@pytest.mark.parametrize(
    ("findings", "rejected", "expected"),
    [
        pytest.param((_BLOCKER, _MAJOR), (), STICKY_FOOTER, id="all-inline"),
        pytest.param((_NIT, _NOTE), (), _NONE_INLINE, id="none-inline-by-routing"),
        pytest.param(
            (_BLOCKER, _MAJOR),
            (_BLOCKER, _MAJOR),
            _NONE_INLINE,
            id="none-inline-batch-rejected",
        ),
        pytest.param(
            (_BLOCKER, _NIT, _NOTE),
            (),
            "<sub>🤖 lintro review · 1 finding commented inline · 2 listed above "
            f"only · {_HOW_TO_READ}</sub>",
            id="mixed",
        ),
        pytest.param(
            (_BLOCKER, _MAJOR, _NIT),
            (_MAJOR,),
            "<sub>🤖 lintro review · 1 finding commented inline · 2 listed above "
            f"only · {_HOW_TO_READ}</sub>",
            id="mixed-one-rejected",
        ),
        pytest.param((), (), _NEUTRAL, id="no-findings"),
    ],
)
def test_round_footer_matches_what_was_posted_inline(
    sample_review_result: ReviewResult,
    findings: tuple[ReviewFinding, ...],
    rejected: tuple[ReviewFinding, ...],
    expected: str,
) -> None:
    """The rendered sticky's footer reports the round's inline split.

    Args:
        sample_review_result: Representative review result fixture.
        findings: The round's findings.
        rejected: Findings whose inline comments could not be posted.
        expected: The footer the sticky must end with.
    """
    failure = (
        InlinePostFailure(reason="rejected", findings=rejected) if rejected else None
    )
    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=findings),
            head_sha="abc1234",
            inline_failure=failure,
        ),
    )

    assert_that(_footer(body)).is_equal_to(expected)


def test_state_only_board_makes_no_inline_claim() -> None:
    """A board re-rendered from stored state does not claim inline comments."""
    body = render_state_sticky(state=ReviewState())

    assert_that(_footer(body)).is_equal_to(_NEUTRAL)
    assert_that(body).does_not_contain("commented inline")


def _posting_reporter(
    *,
    inline_status: int,
    update_answers: tuple[bool, ...],
) -> MagicMock:
    """Build a reporter whose sticky edits GitHub accepts or refuses in turn.

    The sticky already exists, so the write before the inline POST and the
    refresh after it are both edits. ``visible`` holds the body GitHub last
    accepted, which is what a reader of the pull request sees.

    Args:
        inline_status: HTTP status the inline review POST receives.
        update_answers: Whether each successive sticky edit is accepted.

    Returns:
        The configured mock reporter.
    """
    reporter = MagicMock()
    use_combined_comment_lookup(reporter)
    reporter.is_available.return_value = True
    reporter.find_issue_comment.return_value = (77, "")
    diff_lines = {"src/main.py": {10}, "tests/test_main.py": {5}}
    reporter.fetch_pr_diff_lines.return_value = diff_lines
    reporter.fetch_compare_lines.return_value = diff_lines
    reporter.fetch_pr_commit_shas.return_value = []
    answers = list(update_answers)
    reporter.visible = ""

    def _update_issue_comment(**kwargs: Any) -> bool:
        """Accept or refuse a sticky edit, keeping the last accepted body.

        Args:
            **kwargs: Update arguments, of which ``body`` is recorded.

        Returns:
            bool: The next scripted answer.
        """
        accepted = answers.pop(0)
        if accepted:
            reporter.visible = str(kwargs["body"])
        return accepted

    reporter.update_issue_comment.side_effect = _update_issue_comment
    reporter.delete_issue_comment.return_value = True
    reporter.api_response.return_value = GitHubApiResponse(
        status=inline_status,
        message="" if inline_status == 200 else "Forbidden",
    )
    reporter.api_base = "https://api.github.com"
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


@pytest.mark.parametrize(
    ("inline_status", "refresh_accepted", "counted"),
    [
        pytest.param(403, False, False, id="inline-rejected-refresh-rejected"),
        pytest.param(200, False, False, id="inline-accepted-refresh-rejected"),
        pytest.param(200, True, True, id="inline-accepted-refresh-accepted"),
    ],
)
def test_footer_claims_inline_only_after_the_post_is_confirmed(
    sample_review_result: ReviewResult,
    inline_status: int,
    refresh_accepted: bool,
    counted: bool,
) -> None:
    """The visible sticky never claims inline comments ahead of delivery.

    The first sticky write precedes the inline POST, so it carries the
    neutral footer; only an accepted refresh after a successful POST swaps
    in the counted one.

    Args:
        sample_review_result: Representative review result fixture.
        inline_status: HTTP status the inline review POST receives.
        refresh_accepted: Whether GitHub accepts the sticky refresh.
        counted: Whether the visible footer should be the counted one.
    """
    reporter = _posting_reporter(
        inline_status=inline_status,
        update_answers=(True, refresh_accepted),
    )

    post_review_to_github(result=sample_review_result, reporter=reporter)

    inline, sticky_only = inline_split(result=sample_review_result, inline_failure=None)
    expected = (
        sticky_footer(inline=inline, sticky_only=sticky_only) if counted else _NEUTRAL
    )
    assert_that(inline).is_greater_than(0)
    assert_that(reporter.update_issue_comment.call_count).is_equal_to(2)
    assert_that(_footer(reporter.visible)).is_equal_to(expected)
