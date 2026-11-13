"""JSON and MCP report inline delivery apart from inline eligibility (#3426).

``posted_inline`` is set by the posting policy before anything is posted, so
it says a finding *could* open a thread. ``inline_delivered`` says whether one
was actually opened: ``True`` / ``False`` once posting ran, ``None`` when it
never did.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.github import post_review_to_github
from lintro.ai.review.models.inline_delivery import InlineDelivery
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_post_options import ReviewPostOptions
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.output import render_review_json
from lintro.ai.review.sticky.footer import inline_split
from lintro.mcp.toolkits.review import _findings_payload
from tests.unit.ai.review.test_sticky_footer_3410 import _posting_reporter


def _post(
    *,
    result: ReviewResult,
    inline_status: int,
) -> list[dict[str, Any]]:
    """Post *result* through the reporter double and return its JSON findings.

    Args:
        result: The round to post.
        inline_status: HTTP status the inline review POST receives.

    Returns:
        The ``findings`` array of the JSON the CLI would print afterwards.
    """
    reporter = _posting_reporter(
        inline_status=inline_status,
        update_answers=(True, True),
    )
    delivery = InlineDelivery()
    post_review_to_github(
        result=result,
        reporter=reporter,
        options=ReviewPostOptions(inline_delivery=delivery),
    )
    document = json.loads(render_review_json(result=delivery.stamp(result=result)))
    findings: list[dict[str, Any]] = document["findings"]
    return findings


def test_a_rejected_inline_post_delivers_no_finding(
    sample_review_result: ReviewResult,
) -> None:
    """After GitHub refuses the inline review, nothing claims delivery.

    Args:
        sample_review_result: Representative review result fixture.
    """
    findings = _post(result=sample_review_result, inline_status=403)

    assert_that(findings).is_not_empty()
    assert_that([f["posted_inline"] for f in findings]).contains_only(True)
    assert_that([f["inline_delivered"] for f in findings]).contains_only(False)


def test_an_accepted_inline_post_marks_only_the_threads_it_opened(
    sample_review_result: ReviewResult,
) -> None:
    """Delivery follows what was posted, and agrees with the footer's count.

    A low-confidence note, a P3 and a finding off the diff are all left out
    of the inline review, so they are not delivered even though the round
    posted successfully. The delivered count is the one the sticky footer
    reports as "commented inline" (#3410).

    Args:
        sample_review_result: Representative review result fixture.
    """
    base = sample_review_result.findings[0]
    extra = (
        replace(base, title="Only a note", posted_inline=False),
        replace(base, title="A nit", severity=Severity.P3),
        replace(base, title="Off the diff", line=999),
    )
    result = replace(
        sample_review_result,
        findings=(*sample_review_result.findings, *extra),
    )

    findings = _post(result=result, inline_status=200)

    delivered = {f["title"]: f["inline_delivered"] for f in findings}
    assert_that(delivered).is_equal_to(
        {
            **{f.title: True for f in sample_review_result.findings},
            "Only a note": False,
            "A nit": False,
            "Off the diff": False,
        },
    )
    unmappable = InlinePostFailure(findings=(extra[2],))
    inline, _ = inline_split(result=result, inline_failure=unmappable)
    assert_that(sum(1 for value in delivered.values() if value)).is_equal_to(inline)


def test_a_run_without_github_context_reports_delivery_not_attempted(
    sample_review_result: ReviewResult,
) -> None:
    """A poster that never reaches GitHub leaves delivery ``null``.

    Args:
        sample_review_result: Representative review result fixture.
    """
    reporter = _posting_reporter(inline_status=200, update_answers=())
    reporter.is_available.return_value = False
    delivery = InlineDelivery()

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
        options=ReviewPostOptions(inline_delivery=delivery),
    )

    document = json.loads(
        render_review_json(result=delivery.stamp(result=sample_review_result)),
    )
    assert_that(posted).is_false()
    assert_that([f["inline_delivered"] for f in document["findings"]]).contains_only(
        None,
    )


@pytest.mark.parametrize(
    "posted_inline",
    [True, False],
    ids=["eligible", "routed-to-notes"],
)
def test_mcp_reports_eligibility_and_never_claims_delivery(
    sample_review_result: ReviewResult,
    posted_inline: bool,
) -> None:
    """The MCP tool never posts, so delivery is always ``None``.

    Args:
        sample_review_result: Representative review result fixture.
        posted_inline: The policy's eligibility flag on every finding.
    """
    findings: tuple[ReviewFinding, ...] = tuple(
        replace(finding, posted_inline=posted_inline)
        for finding in sample_review_result.findings
    )

    payload = _findings_payload(findings=findings)

    assert_that([f["posted_inline"] for f in payload]).contains_only(posted_inline)
    assert_that([f["inline_delivered"] for f in payload]).contains_only(None)
