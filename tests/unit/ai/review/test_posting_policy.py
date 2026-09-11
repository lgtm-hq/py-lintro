"""Tests for the confidence gate on inline posting (#2572)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import derive_verdict, match_findings
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.posting_policy import (
    PostingPolicy,
    apply_posting_policy,
    coerce_confidence,
    describe_notes,
    inline_findings,
    note_findings,
    select_inline,
)
from lintro.ai.review.sticky import build_sticky_comment
from lintro.ai.review.verdict import derive_readiness_verdict
from lintro.enums.confidence_level import ConfidenceLevel


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a review finding for policy tests.

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


def _mixed_findings() -> tuple[ReviewFinding, ...]:
    """Build one finding per policy outcome, in a fixed order.

    Returns:
        High, low, question, medium — so order preservation is observable.
    """
    return (
        _finding(title="high", confidence="high", line=1),
        _finding(title="low", confidence="low", line=2, severity=Severity.P1),
        _finding(
            title="question",
            confidence="high",
            line=3,
            kind=FindingKind.QUESTION,
        ),
        _finding(title="medium", confidence="medium", line=4),
    )


def _titles(findings: tuple[ReviewFinding, ...]) -> list[str]:
    """Return finding titles in order."""
    return [finding.title for finding in findings]


# --- select_inline -----------------------------------------------------------


def test_default_policy_posts_high_and_medium_findings_only() -> None:
    """Low confidence and questions go to notes; the rest post inline."""
    inline, notes = select_inline(_mixed_findings(), PostingPolicy())

    assert_that(_titles(inline)).is_equal_to(["high", "medium"])
    assert_that(_titles(notes)).is_equal_to(["low", "question"])


def test_select_inline_preserves_input_order_in_both_halves() -> None:
    """Neither half is re-sorted: payload order is the model's order."""
    findings = tuple(reversed(_mixed_findings()))

    inline, notes = select_inline(findings, PostingPolicy())

    assert_that(_titles(inline)).is_equal_to(["medium", "high"])
    assert_that(_titles(notes)).is_equal_to(["question", "low"])


def test_select_inline_places_every_finding_in_exactly_one_half() -> None:
    """Nothing is dropped or duplicated by the split."""
    findings = _mixed_findings()

    inline, notes = select_inline(findings, PostingPolicy())

    assert_that(len(inline) + len(notes)).is_equal_to(len(findings))
    assert_that(set(inline) & set(notes)).is_empty()


@pytest.mark.parametrize(
    ("floor", "expected_inline"),
    [
        (ConfidenceLevel.LOW, ["high", "low", "medium"]),
        (ConfidenceLevel.MEDIUM, ["high", "medium"]),
        (ConfidenceLevel.HIGH, ["high"]),
    ],
    ids=["floor=low", "floor=medium", "floor=high"],
)
def test_inline_min_confidence_moves_the_floor(
    floor: ConfidenceLevel,
    expected_inline: list[str],
) -> None:
    """The floor is inclusive and questions stay gated regardless of it."""
    policy = PostingPolicy(inline_min_confidence=floor)

    inline, _notes = select_inline(_mixed_findings(), policy)

    assert_that(_titles(inline)).is_equal_to(expected_inline)


def test_post_questions_inline_lets_a_question_through_the_gate() -> None:
    """Opting questions in posts them inline; the confidence floor still holds."""
    policy = PostingPolicy(post_questions_inline=True)

    inline, notes = select_inline(_mixed_findings(), policy)

    assert_that(_titles(inline)).is_equal_to(["high", "question", "medium"])
    assert_that(_titles(notes)).is_equal_to(["low"])


def test_low_confidence_question_stays_a_note_even_when_questions_post() -> None:
    """A question below the floor is gated by confidence, not by kind."""
    policy = PostingPolicy(post_questions_inline=True)
    question = _finding(kind=FindingKind.QUESTION, confidence="low")

    inline, notes = select_inline((question,), policy)

    assert_that(inline).is_empty()
    assert_that(notes).is_equal_to((question,))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("high", ConfidenceLevel.HIGH),
        (" Medium ", ConfidenceLevel.MEDIUM),
        ("LOW", ConfidenceLevel.LOW),
        ("very sure", ConfidenceLevel.MEDIUM),
        ("", ConfidenceLevel.MEDIUM),
        (None, ConfidenceLevel.MEDIUM),
        (ConfidenceLevel.LOW, ConfidenceLevel.LOW),
    ],
    ids=["high", "padded-medium", "upper-low", "unknown", "empty", "none", "enum"],
)
def test_coerce_confidence_matches_the_parser_fallback(
    raw: object,
    expected: ConfidenceLevel,
) -> None:
    """Unrecognized values read as medium, the same default the parser uses."""
    assert_that(coerce_confidence(raw=raw)).is_equal_to(expected)


# --- apply_posting_policy ----------------------------------------------------


def test_apply_posting_policy_marks_findings_in_place_and_keeps_order() -> None:
    """The flag is carried on each finding; the sequence is otherwise unchanged."""
    findings = _mixed_findings()

    marked = apply_posting_policy(findings=findings, policy=PostingPolicy())

    assert_that(_titles(marked)).is_equal_to(_titles(findings))
    assert_that([finding.posted_inline for finding in marked]).is_equal_to(
        [True, False, False, True],
    )
    assert_that(_titles(inline_findings(findings=marked))).is_equal_to(
        ["high", "medium"],
    )
    assert_that(_titles(note_findings(findings=marked))).is_equal_to(
        ["low", "question"],
    )


def test_apply_posting_policy_resets_a_stale_flag() -> None:
    """A finding already marked as a note is re-evaluated, not trusted."""
    stale = _finding(confidence="high", posted_inline=False)

    (marked,) = apply_posting_policy(findings=(stale,), policy=PostingPolicy())

    assert_that(marked.posted_inline).is_true()


def test_finding_defaults_to_posted_inline() -> None:
    """A finding that never met the policy renders as it always did."""
    assert_that(_finding().posted_inline).is_true()


def test_describe_notes_counts_gated_findings() -> None:
    """The summary label carries the count and is empty when nothing is gated."""
    marked = apply_posting_policy(findings=_mixed_findings(), policy=PostingPolicy())

    assert_that(describe_notes(findings=marked)).is_equal_to(
        "Notes and questions (2)",
    )
    assert_that(describe_notes(findings=_mixed_findings())).is_empty()


# --- config ------------------------------------------------------------------


def test_policy_from_ai_config_reads_the_defaults() -> None:
    """The default configuration is the documented default policy."""
    policy = PostingPolicy.from_ai_config(AIConfig())

    assert_that(policy).is_equal_to(PostingPolicy())
    assert_that(policy.inline_min_confidence).is_equal_to(ConfidenceLevel.MEDIUM)
    assert_that(policy.post_questions_inline).is_false()


def test_policy_from_ai_config_reads_the_review_keys() -> None:
    """``ai.review_inline_min_confidence`` and ``ai.review_post_questions_inline``."""
    config = AIConfig.model_validate(
        {
            "review_inline_min_confidence": "high",
            "review_post_questions_inline": True,
        },
    )

    policy = PostingPolicy.from_ai_config(config)

    assert_that(policy.inline_min_confidence).is_equal_to(ConfidenceLevel.HIGH)
    assert_that(policy.post_questions_inline).is_true()


def test_config_rejects_an_unknown_confidence_floor() -> None:
    """The floor is validated against the confidence enum."""
    with pytest.raises(ValueError, match="review_inline_min_confidence"):
        AIConfig.model_validate({"review_inline_min_confidence": "certain"})


# --- verdict -----------------------------------------------------------------


def test_verdict_ignores_a_low_confidence_p1_routed_to_notes() -> None:
    """A gated P1 cannot block: the verdict is derived from the inline subset."""
    marked = apply_posting_policy(
        findings=(
            _finding(severity=Severity.P1, confidence="low"),
            _finding(severity=Severity.P3, confidence="high"),
        ),
        policy=PostingPolicy(),
    )

    assert_that(derive_readiness_verdict(findings=marked)).is_equal_to(
        ReviewVerdict.NITS_ONLY,
    )


def test_exit_gate_agrees_with_the_verdict_on_a_gated_p1(
    sample_review_result: ReviewResult,
) -> None:
    """``has_p1_findings`` and ``readiness_verdict`` read the same subset."""
    result = replace(
        sample_review_result,
        findings=apply_posting_policy(
            findings=(_finding(severity=Severity.P1, confidence="low"),),
            policy=PostingPolicy(),
        ),
    )

    assert_that(result.has_p1_findings).is_false()
    assert_that(result.readiness_verdict).is_equal_to(ReviewVerdict.READY)


def test_match_findings_never_tracks_a_note() -> None:
    """Notes get no record, so they cannot be carried, resolved, or counted."""
    marked = apply_posting_policy(findings=_mixed_findings(), policy=PostingPolicy())

    match = match_findings(previous=None, findings=marked, round_number=1)

    assert_that([record.title for record in match.records]).is_equal_to(
        ["high", "medium"],
    )
    assert_that(derive_verdict(findings=match.records)).is_equal_to(
        ReviewVerdict.CHANGES_REQUESTED,
    )


# --- surfaces ----------------------------------------------------------------


def _gated_result(sample_review_result: ReviewResult) -> ReviewResult:
    """Return the sample result carrying the mixed findings, policy applied."""
    return replace(
        sample_review_result,
        findings=apply_posting_policy(
            findings=_mixed_findings(),
            policy=PostingPolicy(),
        ),
    )


def test_sticky_renders_a_collapsed_notes_block_with_file_links(
    sample_review_result: ReviewResult,
) -> None:
    """Gated findings land in one ``<details>`` block, linked to ``file:line``."""
    body = build_sticky_comment(
        request=StickyRequest(
            result=_gated_result(sample_review_result),
            head_sha="abc123def456",
            repo="lgtm-hq/py-lintro",
            pr_number=7,
        ),
    )

    assert_that(body).contains("<details><summary>💬 Notes and questions (2)</summary>")
    assert_that(body).contains(
        "[`src/app.py:2`](https://github.com/lgtm-hq/py-lintro/blob/abc123def456/src/app.py#L2)",
    )
    assert_that(body).contains("🔴 P1 · low confidence — **low**")
    assert_that(body).contains("❓ question — **question**")
    # The notes never reach the open-findings table or the tiles.
    assert_that(body).contains("· 2 open · ")
    assert_that(body).does_not_contain("| low |")
    assert_that(body).does_not_contain("| question |")


def test_sticky_note_renders_unlinked_when_the_path_needs_sanitizing(
    sample_review_result: ReviewResult,
) -> None:
    """A model-written path that cannot sit in a Markdown link stays plain text."""
    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(
                sample_review_result,
                findings=(
                    _finding(
                        title="odd path",
                        confidence="low",
                        file="src/we|rd).py",
                        posted_inline=False,
                    ),
                ),
            ),
            head_sha="abc123def456",
            repo="lgtm-hq/py-lintro",
            pr_number=7,
        ),
    )

    assert_that(body).contains("**odd path** · `src/we\\|rd).py:12`")
    assert_that(body).does_not_contain("blob/abc123def456/src/we")


def test_sticky_omits_the_notes_block_when_nothing_was_gated(
    sample_review_result: ReviewResult,
) -> None:
    """A round with every finding posted inline renders exactly as before."""
    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=(_finding(),)),
        ),
    )

    assert_that(body).does_not_contain("Notes and questions")


def test_sticky_verdict_ignores_notes(sample_review_result: ReviewResult) -> None:
    """The gated P1 does not turn the board's title into Blocked."""
    body = build_sticky_comment(
        request=StickyRequest(result=_gated_result(sample_review_result)),
    )

    assert_that(body).contains("## 🔎 Lintro Review — ⚠️ Changes requested")


def test_review_body_header_counts_inline_findings_only(
    sample_review_result: ReviewResult,
) -> None:
    """The header announces threads below it, so notes are not counted."""
    result = _gated_result(sample_review_result)
    body = build_review_body(
        result=result,
        prior_state=ReviewState(),
        match=match_findings(previous=None, findings=result.findings, round_number=1),
        head_sha="abc123def456",
    )

    assert_that(body).contains("2 findings posted**")
    assert_that(body).does_not_contain("**low**")
    assert_that(body).does_not_contain("**question**")


# --- payload -----------------------------------------------------------------


def test_json_payload_keeps_every_finding_and_flags_posted_inline(
    sample_review_result: ReviewResult,
) -> None:
    """Gating is visible in the payload, never an omission from it."""
    payload = review_result_to_dict(result=_gated_result(sample_review_result))

    assert_that(payload["findings"]).is_length(4)
    assert_that(
        [(item["title"], item["posted_inline"]) for item in payload["findings"]],
    ).is_equal_to(
        [("high", True), ("low", False), ("question", False), ("medium", True)],
    )
    assert_that(payload["readiness_verdict"]).is_equal_to("changes_requested")


def test_match_result_type_is_unchanged_by_the_gate() -> None:
    """Guard: the gate filters inputs, it does not change the match contract."""
    match = match_findings(previous=None, findings=(), round_number=1)

    assert_that(match).is_instance_of(FindingMatchResult)
