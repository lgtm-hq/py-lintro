"""Tests for the severity posting tiers (lintro-ops #37, decision A).

P1/P2 findings open inline threads; P3 nits are listed in the sticky under
a disclosure, stay tracked, and still read as fixed once they go away.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from assertpy import assert_that

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.finding_matcher import (
    match_findings,
    review_findings_from_unposted,
)
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.posting_policy import PostingPolicy, apply_posting_policy
from lintro.ai.review.posting_tiers import (
    INLINE_SEVERITIES,
    inline_tier_findings,
    is_inline_tier,
    split_records_by_tier,
)
from lintro.ai.review.sticky import build_sticky_comment

NIT_SUMMARY = "🟡 1 P3 nit (not posted inline)"


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a high-confidence finding for tier tests.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The constructed finding.
    """
    fields: dict[str, Any] = {
        "severity": Severity.P2,
        "category": "logic-bug",
        "file": "src/app.py",
        "line": 12,
        "title": "Something is wrong",
        "description": "The branch is never taken.",
        "cause": "Off by one.",
        "fix": "Compare with >=.",
        "confidence": "high",
    }
    fields.update(overrides)
    return ReviewFinding(**fields)


def _tiered(*findings: ReviewFinding) -> tuple[ReviewFinding, ...]:
    """Apply the default posting policy so ``posted_inline`` is set."""
    return apply_posting_policy(findings=findings, policy=PostingPolicy())


def _p2() -> ReviewFinding:
    return _finding(title="Real defect", line=5)


def _p3() -> ReviewFinding:
    return _finding(title="Nit title", line=9, severity=Severity.P3)


# --- tier constant and selectors ---------------------------------------------


def test_inline_tier_is_p1_and_p2_only() -> None:
    """The boundary is one constant: P1 and P2 in, P3 out."""
    assert_that(INLINE_SEVERITIES).is_equal_to(frozenset({Severity.P1, Severity.P2}))
    assert_that(is_inline_tier(severity=Severity.P1)).is_true()
    assert_that(is_inline_tier(severity=Severity.P2)).is_true()
    assert_that(is_inline_tier(severity=Severity.P3)).is_false()


def test_inline_tier_findings_drops_p3_after_the_policy() -> None:
    """A P3 that clears the confidence gate still gets no thread."""
    findings = _tiered(_p2(), _p3(), _finding(title="low", confidence="low"))

    selected = inline_tier_findings(findings=findings)

    assert_that([finding.title for finding in selected]).is_equal_to(["Real defect"])
    # The tier is rendering only: the P3 keeps ``posted_inline`` so the
    # matcher tracks it like any other finding.
    assert_that([f.posted_inline for f in findings]).is_equal_to([True, True, False])


def test_split_records_by_tier_preserves_order() -> None:
    """Records split into inline and sticky-only halves without re-sorting."""
    records = (
        FindingRecord(fingerprint="a", severity=Severity.P3, title="a"),
        FindingRecord(fingerprint="b", severity=Severity.P1, title="b"),
        FindingRecord(fingerprint="c", severity=Severity.P3, title="c"),
        FindingRecord(fingerprint="d", severity=Severity.P2, title="d"),
    )

    inline, nits = split_records_by_tier(records=records)

    assert_that([r.title for r in inline]).is_equal_to(["b", "d"])
    assert_that([r.title for r in nits]).is_equal_to(["a", "c"])


# --- review body header --------------------------------------------------------


def test_review_body_header_counts_threads_not_nits(
    sample_review_result: ReviewResult,
) -> None:
    """One P2 and one P3 announce one finding posted: the P3 has no thread."""
    result = replace(sample_review_result, findings=_tiered(_p2(), _p3()))
    match = match_findings(previous=None, findings=result.findings, round_number=1)

    body = build_review_body(result=result, prior_state=ReviewState(), match=match)

    assert_that(body).contains("1 finding posted**")


# --- sticky rendering --------------------------------------------------------------


def test_sticky_lists_p3_under_a_disclosure_and_p2_in_the_table(
    sample_review_result: ReviewResult,
) -> None:
    """The Δ table carries the P2; the P3 sits under the nits disclosure."""
    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=_tiered(_p2(), _p3())),
            head_sha="abc123def456",
            repo="lgtm-hq/py-lintro",
            pr_number=7,
        ),
    )

    assert_that(body).contains("| Δ | Sev | Finding | Where | Since |")
    assert_that(body).contains("Real defect")
    assert_that(body).contains(f"<details><summary>{NIT_SUMMARY}</summary>")
    assert_that(body).contains("| Δ | Finding | Where |")
    assert_that(body).contains(
        "| **new** | **Nit title**<br>The branch is never taken.<br>"
        "Fix: Compare with >=. | `src/app.py:9` |",
    )
    # Both are open and tracked: the heading counts two.
    assert_that(body).contains("· 2 open · ")
    # The nit row is not in the severity-bearing table.
    table = body.split("| Δ | Sev | Finding | Where | Since |", 1)[1]
    table = table.split("<details>", 1)[0]
    assert_that(table).does_not_contain("Nit title")


def test_sticky_omits_the_nits_block_when_no_p3_is_open(
    sample_review_result: ReviewResult,
) -> None:
    """A round without P3s renders no disclosure at all."""
    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=_tiered(_p2())),
        ),
    )

    assert_that(body).does_not_contain("(not posted inline)</summary>")


def test_a_nit_cannot_close_the_disclosure_with_model_written_html(
    sample_review_result: ReviewResult,
) -> None:
    """A ``</details>`` in a nit's own text is defanged, not rendered.

    The nit rows sit inside ``_nits_block``'s disclosure, so untrusted model
    text there must not be able to end it early and re-nest the rest of the
    comment. The table-cell sanitizer alone does not defang these tags.
    """
    escaping = replace(
        _p3(),
        description="Ends the block: </details><summary>injected</summary>",
        fix="And again </details>",
    )
    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=_tiered(escaping)),
        ),
    )

    # Scoped to the disclosure: the fix-all prompt panel renders the same
    # text inside a ``` fence, where the tags are literal by construction and
    # the raw sequence is expected to appear.
    nits = body.split("(not posted inline)</summary>", 1)[1]
    nits = nits.split("</details>", 1)[0]

    assert_that(nits).contains("&lt;/details")
    assert_that(nits).contains("&lt;summary")
    # The model's own sequence never reaches the cell as real tags, which is
    # what closing the disclosure early would have taken.
    assert_that(nits).does_not_contain("</details><summary>injected")


def test_sticky_pluralizes_the_nits_summary(
    sample_review_result: ReviewResult,
) -> None:
    """Two nits read as nits."""
    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(
                sample_review_result,
                findings=_tiered(_p3(), replace(_p3(), title="Other nit", line=20)),
            ),
        ),
    )

    assert_that(body).contains("🟡 2 P3 nits (not posted inline)")


# --- lifecycle -------------------------------------------------------------------


def test_a_fixed_p3_still_renders_as_fixed_without_ever_having_a_thread(
    sample_review_result: ReviewResult,
) -> None:
    """A P3 is tracked by key, so dropping it next round reads as fixed."""
    round_one = match_findings(
        previous=None,
        findings=_tiered(_p3()),
        round_number=1,
        reviewed_paths=frozenset({"src/app.py"}),
    )
    (record,) = round_one.records
    assert_that(record.severity).is_equal_to(Severity.P3)
    assert_that(record.inline_comment_id).is_none()
    prior = ReviewState(findings=round_one.records)

    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=_tiered(_p2())),
            prior_state=prior,
            head_sha="abc123def456",
        ),
    )

    assert_that(body).contains("| ✔ fixed | 🟡 P3 | ~~Nit title~~ |")
    assert_that(body).does_not_contain("(not posted inline)</summary>")


def test_resume_replay_never_reposts_a_p3_record() -> None:
    """Records outside the inline tier are not rebuilt as findings to post."""
    prior = ReviewState(
        findings=(
            FindingRecord(
                fingerprint="nit",
                severity=Severity.P3,
                title="Nit title",
                file="src/other.py",
                line=3,
                status=FindingStatus.OPEN,
                description="d",
            ),
            FindingRecord(
                fingerprint="defect",
                severity=Severity.P2,
                title="Real defect",
                file="src/other.py",
                line=4,
                status=FindingStatus.OPEN,
                description="d",
            ),
        ),
    )

    replayed = review_findings_from_unposted(
        prior=prior,
        current=(),
        reviewed_paths=frozenset(),
    )

    assert_that([finding.title for finding in replayed]).is_equal_to(["Real defect"])
