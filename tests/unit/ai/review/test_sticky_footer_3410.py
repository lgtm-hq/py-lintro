"""The sticky footer claims inline comments only when some were posted (#3410)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.github_constants import STICKY_FOOTER
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky import build_sticky_comment, render_state_sticky

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
