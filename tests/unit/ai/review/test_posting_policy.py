"""Tests for the confidence gate on inline posting (#2572)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import (
    count_blocking_findings,
    derive_verdict,
    match_findings,
)
from lintro.ai.review.github_constants import STICKY_FOOTER
from lintro.ai.review.github_contract import MAX_COMMENT_CHARS, TRUNCATION_NOTICE
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.lifecycle.markers import file_line_url
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


# --- cross-round carry (review thread on #2583) --------------------------------


def _round(
    *,
    previous: ReviewState | None,
    finding: ReviewFinding,
    round_number: int,
) -> FindingMatchResult:
    """Match one round carrying a single policy-marked finding."""
    (marked,) = apply_posting_policy(findings=(finding,), policy=PostingPolicy())
    return match_findings(
        previous=previous,
        findings=(marked,),
        round_number=round_number,
        head_sha=f"sha{round_number}",
        reviewed_paths=frozenset({finding.file}),
    )


def _state_after(match: FindingMatchResult, *, posted: bool = True) -> ReviewState:
    """Persist a round's records the way the sticky state would.

    Args:
        match: The round's matching outcome.
        posted: When True, stamp an inline comment id on every open record, as
            the post path does once GitHub accepts the review batch. The
            carried-note tag keys on that id, because only a posted thread can
            be the thread the note keeps open.

    Returns:
        The state the next round loads.
    """
    records = match.records
    if posted:
        records = tuple(
            (
                replace(record, inline_comment_id=index + 1)
                if record.status is FindingStatus.OPEN
                else record
            )
            for index, record in enumerate(records)
        )
    return ReviewState(findings=records)


def test_a_prior_inline_record_stays_open_when_re_reported_as_a_note() -> None:
    """Dropping below the floor is not a fix: the thread is carried, not resolved."""
    blocker = _finding(severity=Severity.P1, confidence="high")

    round_one = _round(previous=None, finding=blocker, round_number=1)
    round_two = _round(
        previous=_state_after(round_one),
        finding=replace(blocker, confidence="low"),
        round_number=2,
    )

    (record,) = round_two.records
    assert_that(record.status).is_equal_to(FindingStatus.OPEN)
    assert_that(record.resolved_round).is_equal_to(0)
    assert_that(round_two.resolved).is_empty()
    assert_that(round_two.new).is_empty()
    assert_that([carried.key for carried in round_two.carried]).is_equal_to(
        [record.key],
    )
    assert_that(round_two.outcome_for(record=record)).is_equal_to(
        FindingMatchOutcome.CARRIED,
    )


def test_a_note_that_recovers_confidence_is_carried_not_new() -> None:
    """Round 3 at high confidence matches the still-open record from round 1."""
    blocker = _finding(severity=Severity.P1, confidence="high")
    round_one = _round(previous=None, finding=blocker, round_number=1)
    round_two = _round(
        previous=_state_after(round_one),
        finding=replace(blocker, confidence="low"),
        round_number=2,
    )

    round_three = _round(
        previous=_state_after(round_two),
        finding=blocker,
        round_number=3,
    )

    (record,) = round_three.records
    assert_that(round_three.new).is_empty()
    assert_that(record.since_round).is_equal_to(1)
    assert_that(record.status).is_equal_to(FindingStatus.OPEN)


def test_a_prior_record_resolves_only_when_its_fingerprint_is_absent() -> None:
    """The carry is fingerprint-scoped: an unrelated note resolves nothing."""
    blocker = _finding(severity=Severity.P1, confidence="high")
    round_one = _round(previous=None, finding=blocker, round_number=1)

    round_two = _round(
        previous=_state_after(round_one),
        finding=_finding(title="Different thing", confidence="low"),
        round_number=2,
    )

    assert_that([record.status for record in round_two.records]).is_equal_to(
        [FindingStatus.RESOLVED],
    )


def test_sticky_tags_a_note_that_kept_a_prior_thread_open(
    sample_review_result: ReviewResult,
) -> None:
    """The reader learns why round 2's thread was not resolved."""
    blocker = _finding(severity=Severity.P1, confidence="high", title="Kept open")
    round_one = _round(previous=None, finding=blocker, round_number=1)
    (note,) = apply_posting_policy(
        findings=(replace(blocker, confidence="low"),),
        policy=PostingPolicy(),
    )

    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=(note,)),
            prior_state=_state_after(round_one),
        ),
    )

    assert_that(body).contains(
        "**Kept open** · `src/app.py:12` (below the inline confidence floor "
        "this round)",
    )
    assert_that(body).contains("0 fixed this round")


# --- link encoding and caption (review threads on #2583) ------------------------


@pytest.mark.parametrize(
    ("path", "encoded"),
    [
        ("docs/my notes.md", "docs/my%20notes.md"),
        ("src/weird).py", "src/weird%29.py"),
        ("src/(group)/a.py", "src/%28group%29/a.py"),
    ],
    ids=["space", "lone-paren", "balanced-parens"],
)
def test_note_link_percent_encodes_characters_commonmark_cannot_take(
    sample_review_result: ReviewResult,
    path: str,
    encoded: str,
) -> None:
    """A space or a parenthesis in the path must not end the link early."""
    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(
                sample_review_result,
                findings=(
                    _finding(
                        title="odd path",
                        confidence="low",
                        file=path,
                        posted_inline=False,
                    ),
                ),
            ),
            head_sha="abc123def456",
            repo="lgtm-hq/py-lintro",
            pr_number=7,
        ),
    )

    assert_that(body).contains(
        f"[`{path}:12`](https://github.com/lgtm-hq/py-lintro/blob/abc123def456/"
        f"{encoded}#L12)",
    )


def test_file_line_url_encodes_the_path_and_keeps_the_route() -> None:
    """Slashes stay literal; everything CommonMark would choke on is encoded."""
    url = file_line_url(
        repo="o/r",
        sha="deadbeef",
        path="a b/c(d).py",
        line=3,
    )

    assert_that(url).is_equal_to(
        "https://github.com/o/r/blob/deadbeef/a%20b/c%28d%29.py#L3",
    )


def _notes_body(*, result: ReviewResult, policy: PostingPolicy) -> str:
    """Render a sticky under the given policy and return its notes block."""
    body = build_sticky_comment(
        request=StickyRequest(result=result, posting_policy=policy),
    )
    return body.split("💬 Notes and questions", 1)[1].split("</details>", 1)[0]


def test_notes_caption_names_the_configured_floor(
    sample_review_result: ReviewResult,
) -> None:
    """Under a ``high`` floor the caption says so instead of claiming ``low``."""
    policy = PostingPolicy(inline_min_confidence=ConfidenceLevel.HIGH)
    result = replace(
        sample_review_result,
        findings=apply_posting_policy(findings=_mixed_findings(), policy=policy),
    )

    notes = _notes_body(result=result, policy=policy)

    assert_that(notes).contains(
        "findings below the inline confidence floor (high) and open questions.",
    )
    assert_that(notes).contains("**medium**")


def test_notes_caption_drops_questions_when_they_post_inline(
    sample_review_result: ReviewResult,
) -> None:
    """With questions posted inline the caption does not claim they live here."""
    policy = PostingPolicy(post_questions_inline=True)
    result = replace(
        sample_review_result,
        findings=apply_posting_policy(findings=_mixed_findings(), policy=policy),
    )

    notes = _notes_body(result=result, policy=policy)

    assert_that(notes).contains(
        "findings below the inline confidence floor (medium).",
    )
    assert_that(notes).does_not_contain("open questions")
    assert_that(notes).does_not_contain("**question**")


# --- round 3 review threads on #2583 ------------------------------------------


def test_only_the_sibling_a_note_re_asserts_is_carried() -> None:
    """Two prior records share a fingerprint; one note holds exactly one open.

    Fingerprint membership would carry both, leaving the sibling that stopped
    being reported open forever.
    """
    first = _finding(severity=Severity.P1, confidence="high", line=12)
    second = replace(first, line=90)
    round_one = match_findings(
        previous=None,
        findings=apply_posting_policy(
            findings=(first, second),
            policy=PostingPolicy(),
        ),
        round_number=1,
        reviewed_paths=frozenset({first.file}),
    )
    assert_that(round_one.records).is_length(2)

    round_two = _round(
        previous=_state_after(round_one),
        finding=replace(second, confidence="low"),
        round_number=2,
    )

    by_line = {record.line: record.status for record in round_two.records}
    assert_that(by_line).is_equal_to(
        {90: FindingStatus.OPEN, 12: FindingStatus.RESOLVED},
    )
    assert_that([record.line for record in round_two.resolved]).is_equal_to([12])


def test_a_carried_question_note_says_why_it_was_not_posted(
    sample_review_result: ReviewResult,
) -> None:
    """A question is never below the floor; the tag must not claim it was."""
    question = _finding(
        severity=Severity.P2,
        kind=FindingKind.QUESTION,
        confidence="high",
        title="Is this intended?",
    )
    round_one = match_findings(
        previous=None,
        findings=apply_posting_policy(
            findings=(question,),
            policy=PostingPolicy(post_questions_inline=True),
        ),
        round_number=1,
        reviewed_paths=frozenset({question.file}),
    )
    (note,) = apply_posting_policy(findings=(question,), policy=PostingPolicy())

    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=(note,)),
            prior_state=_state_after(round_one),
        ),
    )

    assert_that(body).contains(
        "**Is this intended?** · `src/app.py:12` (questions are not posted " "inline)",
    )
    assert_that(body).does_not_contain("below the inline confidence floor this")


def test_a_note_matching_an_unposted_record_is_not_tagged(
    sample_review_result: ReviewResult,
) -> None:
    """Only a thread that was actually opened can be the thread still open."""
    blocker = _finding(severity=Severity.P1, confidence="high", title="No thread")
    round_one = _round(previous=None, finding=blocker, round_number=1)
    (note,) = apply_posting_policy(
        findings=(replace(blocker, confidence="low"),),
        policy=PostingPolicy(),
    )

    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=(note,)),
            prior_state=_state_after(round_one, posted=False),
        ),
    )

    assert_that(body).contains("**No thread**")
    assert_that(body).does_not_contain("below the inline confidence floor this")


def test_a_multi_line_note_description_renders_on_one_line(
    sample_review_result: ReviewResult,
) -> None:
    """A newline in the description would drop its tail out of the list item."""
    note = _finding(
        confidence="low",
        title="Wrapped",
        description="First line.\nSecond line.\r\nThird line.",
        posted_inline=False,
    )

    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=(note,)),
        ),
    )

    assert_that(body).contains("  First line. Second line. Third line.")


def test_an_oversized_notes_block_is_pruned_rather_than_truncated(
    sample_review_result: ReviewResult,
) -> None:
    """The notes block shrinks under the size budget and says that it did.

    Without a limit the block is unprunable, so ``fit_body`` exhausts its
    stages and ``cap_body`` hard-truncates the tail — silently cutting the
    fix-all prompt and the footer.
    """
    result = sample_review_result
    notes = tuple(
        _finding(
            confidence="low",
            title=f"Note {index}",
            description="x" * 500,
            file=f"src/mod{index}.py",
            posted_inline=False,
        )
        for index in range(400)
    )
    body = build_sticky_comment(
        request=StickyRequest(result=replace(result, findings=notes)),
    )

    assert_that(len(body)).is_less_than_or_equal_to(MAX_COMMENT_CHARS)
    assert_that(body).contains("more notes not listed")
    assert_that(body).contains(STICKY_FOOTER)
    assert_that(body).does_not_contain(TRUNCATION_NOTICE)


def test_a_demoted_p1_still_counts_as_blocking_for_a_converged_skip(
    sample_review_result: ReviewResult,
) -> None:
    """The skip's exit tracks open threads, not the demotion round's gate.

    The demotion round exits 0 because ``has_p1_findings`` ignores notes, but
    its record is carried open so the thread stays — and a converged skip after
    it reports that thread. Documented on ``_finish_converged_review``; locked
    here so a later change has to change the contract deliberately.
    """
    blocker = _finding(severity=Severity.P1, confidence="high")
    round_one = _round(previous=None, finding=blocker, round_number=1)
    demoted = replace(blocker, confidence="low")

    round_two = _round(
        previous=_state_after(round_one),
        finding=demoted,
        round_number=2,
    )

    result = replace(
        sample_review_result,
        findings=apply_posting_policy(findings=(demoted,), policy=PostingPolicy()),
    )
    assert_that(result.has_p1_findings).is_false()
    assert_that(round_one.records).is_length(1)
    assert_that(count_blocking_findings(findings=round_two.records)).is_equal_to(1)
