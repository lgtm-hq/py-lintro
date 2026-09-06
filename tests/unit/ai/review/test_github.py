"""Tests for the rich GitHub review posting adapter."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.models.github_api_response import GitHubApiResponse
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.github import (
    GITHUB_COMMENT_HARD_LIMIT,
    MAX_COMMENT_CHARS,
    STATE_MARKER_PREFIX,
    STICKY_MARKER,
    ReviewPostOptions,
    _count_new_commits,
    build_sticky_comment,
    format_error_comment,
    format_finding_comment,
    format_run_mechanics,
    parse_sticky_state,
    post_review_error_to_github,
    post_review_to_github,
    sanitize_comment_text,
)
from lintro.ai.review.github_contract import cap_body
from lintro.ai.review.inline_fix import plan_inline_fix
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest


@dataclass
class _ReporterLog:
    """Plain record of the comments a review posting writes.

    The reporter stays a ``MagicMock`` because of its wide surface, but the
    assertions read these lists rather than mock call bookkeeping (#2315).

    Attributes:
        api_calls: ``(method, url, payload)`` of each raw API request.
        issue_comment_bodies: Body of each sticky comment posted or edited.
        api_response: Response every raw API request returns; tests set it to
            a failure status to drive the degraded paths.
        update_outcomes: Results ``update_issue_comment`` returns, consumed in
            order; ``default_update_result`` answers once they run out.
        default_update_result: Result ``update_issue_comment`` falls back to.
    """

    api_calls: list[tuple[str, str, Any]] = field(default_factory=list)
    issue_comment_bodies: list[str] = field(default_factory=list)
    api_response: GitHubApiResponse = field(
        default_factory=lambda: GitHubApiResponse(status=200),
    )
    update_outcomes: list[bool] = field(default_factory=list)
    default_update_result: bool = True


def _fresh_reporter() -> MagicMock:
    """Build a MagicMock reporter with no existing sticky comment.

    Returns:
        MagicMock: The reporter stub. Its ``log`` attribute is a
        :class:`_ReporterLog` recording every comment the run writes.
    """
    reporter = MagicMock()
    log = _ReporterLog()
    reporter.log = log
    reporter.is_available.return_value = True
    reporter.find_issue_comment.return_value = None
    reporter.fetch_pr_diff_lines.return_value = {"src/main.py": {10}}
    reporter.fetch_compare_lines.return_value = {"src/main.py": {10}}
    reporter.fetch_pr_commit_shas.return_value = []

    def _post_issue_comment(body: Any, **_kwargs: Any) -> bool:
        """Record a newly posted sticky body.

        Args:
            body: Sticky comment body the production code posted.
            **_kwargs: Ignored posting extras.

        Returns:
            bool: Always ``True``, the success result GitHub would return.
        """
        log.issue_comment_bodies.append(str(body))
        return True

    reporter.post_issue_comment.side_effect = _post_issue_comment

    def _update_issue_comment(**kwargs: Any) -> bool:
        log.issue_comment_bodies.append(str(kwargs["body"]))
        if log.update_outcomes:
            return log.update_outcomes.pop(0)
        return log.default_update_result

    reporter.update_issue_comment.side_effect = _update_issue_comment
    reporter.delete_issue_comment.return_value = True

    def _api_response(method: str, url: str, payload: Any = None) -> GitHubApiResponse:
        log.api_calls.append((method, url, payload))
        return log.api_response

    reporter.api_response.side_effect = _api_response
    reporter.api_base = "https://api.github.com"
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


# --- formatting: severity badges, chips, fix slot, prompt panel -------------


def test_format_finding_comment_uses_color_badge_and_chips(
    sample_review_result: ReviewResult,
) -> None:
    """Finding comment renders color emoji severity and code chips."""
    finding = sample_review_result.findings[0]
    comment = format_finding_comment(finding=finding)

    assert_that(comment).contains("🔴 **P1**")
    assert_that(comment).contains("`security`")
    assert_that(comment).contains("`high confidence`")
    assert_that(comment).contains("**Fail-open default**")
    assert_that(comment).contains("Default to Expired")


def test_format_finding_comment_emits_suggestion_block(
    sample_review_result: ReviewResult,
) -> None:
    """A validated mode A plan renders a GitHub suggestion block."""
    finding = replace(
        sample_review_result.findings[0],
        suggested_code="    return Status.EXPIRED",
    )
    comment = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(
            finding=finding,
            round_diff_lines={finding.file: {finding.line}},
        ),
    )

    assert_that(comment).contains("```suggestion")
    assert_that(comment).contains("return Status.EXPIRED")


def test_format_finding_comment_linked_includes_review_questions(
    sample_review_result: ReviewResult,
) -> None:
    """Linked mode appends review question bullets to finding comments."""
    finding = sample_review_result.findings[0]
    comment = format_finding_comment(
        finding=finding,
        checklist_display=ChecklistDisplay.LINKED,
        question_map={1: "Does unknown status fail closed?"},
    )

    assert_that(comment).contains("**Review questions:**")
    assert_that(comment).contains("Does unknown status fail closed?")


# --- per-run mechanics + exact/approximate labeling -------------------------


def test_run_mechanics_exact_when_provider_reported(
    sample_review_result: ReviewResult,
) -> None:
    """Exact figures carry no approximate marker."""
    mechanics = format_run_mechanics(metadata=sample_review_result.metadata)

    assert_that(mechanics).contains("$0.0500")
    assert_that(mechanics).contains("provider-reported")
    assert_that(mechanics).does_not_contain("~$")


def test_run_mechanics_marks_estimated_with_tilde(
    sample_review_result: ReviewResult,
) -> None:
    """Estimated token/cost figures are prefixed with a tilde."""
    metadata = replace(
        sample_review_result.metadata,
        token_usage_estimated=True,
    )
    mechanics = format_run_mechanics(metadata=metadata)

    assert_that(mechanics).contains("~$0.0500")
    assert_that(mechanics).contains("estimated")
    assert_that(mechanics).contains("~1,200 tok")


# --- API-error formatting ---------------------------------------------------


def test_format_error_comment_auth() -> None:
    """Authentication errors render a specific message."""
    body = format_error_comment(error=AIAuthenticationError("bad key"))

    assert_that(body).contains("authentication failed")
    assert_that(body).contains("ANTHROPIC_API_KEY")
    assert_that(body).contains(STICKY_MARKER)


def test_format_error_comment_rate_limit() -> None:
    """Rate limit errors mention retry."""
    body = format_error_comment(error=AIRateLimitError("429 too many"))

    assert_that(body).contains("rate-limited")


def test_format_error_comment_quota() -> None:
    """Quota/credit errors are detected from the message text."""
    body = format_error_comment(
        error=AIProviderError("insufficient credit balance"),
    )

    assert_that(body).contains("quota or credits")


# --- sanitization -----------------------------------------------------------


def test_sanitize_strips_mentions() -> None:
    """@mentions are neutralized with a zero-width space."""
    cleaned = sanitize_comment_text("ping @octocat and @team now")

    assert_that(cleaned).does_not_contain("@octocat")
    assert_that(cleaned).contains("@​octocat")


def test_sanitize_caps_length() -> None:
    """Oversized text is truncated to the limit."""
    cleaned = sanitize_comment_text("x" * 500, limit=100)

    assert_that(len(cleaned)).is_less_than_or_equal_to(100)
    assert_that(cleaned).ends_with("…")


def test_finding_mentions_are_neutralized_in_comment() -> None:
    """Injected mentions in model output never survive into the comment."""
    finding = ReviewFinding(
        severity=Severity.P2,
        category="security",
        file="a.py",
        line=1,
        title="Contact @maintainer immediately",
        description="cc @everyone",
        cause="c",
        fix="f",
        confidence="low",
    )
    comment = format_finding_comment(finding=finding)

    assert_that(comment).does_not_contain("@maintainer")
    assert_that(comment).does_not_contain("@everyone")


def test_suggestion_block_neutralizes_mentions(
    sample_review_result: ReviewResult,
) -> None:
    """Mentions inside model-supplied suggested code cannot ping users."""
    finding = replace(
        sample_review_result.findings[0],
        suggested_code="# ping @team\nreturn Status.EXPIRED",
    )
    comment = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(
            finding=finding,
            round_diff_lines={finding.file: {finding.line}},
        ),
    )

    assert_that(comment).contains("```suggestion")
    assert_that(comment).does_not_contain("@team")


# --- sticky comment + cumulative aggregation --------------------------------


def test_build_sticky_comment_has_markers_and_verdict_header(
    sample_review_result: ReviewResult,
) -> None:
    """First-run sticky comment leads with the round and the derived verdict."""
    body = build_sticky_comment(request=StickyRequest(result=sample_review_result))

    assert_that(body).contains(STICKY_MARKER)
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)
    assert_that(body).contains("## 🔎 Lintro Review — ⛔ Blocked")
    # Accounting never precedes the verdict (#1905).
    assert_that(body.index("Lintro Review")).is_less_than(body.index("est. cost"))


def test_build_sticky_comment_aggregates_prior_runs(
    sample_review_result: ReviewResult,
) -> None:
    """Cumulative header sums prior runs and flags mixed estimates."""
    prior = ReviewState(
        runs=(
            RunRecord.from_dict(
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "model": "cursor:auto",
                    "provider": "cursor",
                    "total": 5000,
                    "cost": 0.02,
                    "estimated": True,
                    "depth": 1,
                    "p1": 0,
                    "p2": 1,
                    "p3": 0,
                },
            ),
        ),
    )
    body = build_sticky_comment(
        request=StickyRequest(result=sample_review_result, prior_state=prior),
    )

    assert_that(body).contains("### 🕘 History · 1 previous run")
    # Mixed estimate => cumulative flagged approximate.
    assert_that(body).contains("~$")
    assert_that(body.count("🕘 History")).is_equal_to(1)


def test_round_trip_state_parsing(sample_review_result: ReviewResult) -> None:
    """New stickies carry no leftover blob; parse yields empty state."""
    from lintro.ai.review.sticky import advance_review_state

    body = build_sticky_comment(request=StickyRequest(result=sample_review_result))
    parsed = parse_sticky_state(body=body)
    state = advance_review_state(request=StickyRequest(result=sample_review_result))

    assert_that(parsed.runs).is_empty()
    assert_that(state.runs[0].model).is_equal_to("claude-sonnet-4-20250514")


def test_parse_sticky_state_handles_missing_block() -> None:
    """A body with no state block yields an empty state."""
    assert_that(parse_sticky_state(body="no state here").runs).is_empty()


# --- posting: create, update, inline ----------------------------------------


def test_post_review_creates_sticky_when_absent(
    sample_review_result: ReviewResult,
) -> None:
    """With no existing comment, a new sticky comment is posted."""
    reporter = _fresh_reporter()

    captured: dict[str, int] = {}
    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
        options=ReviewPostOptions(captured_comment_ids=captured),
    )

    assert_that(posted).is_true()
    reporter.post_issue_comment.assert_called_once()
    reporter.update_issue_comment.assert_not_called()
    body = reporter.post_issue_comment.call_args.args[0]
    assert_that(body).contains(STICKY_MARKER)


def test_failed_inline_post_folds_details_into_the_sticky(
    sample_review_result: ReviewResult,
) -> None:
    """A rejected inline batch leaves the sticky as the findings' only surface.

    Without this the PR keeps a verdict whose findings appear nowhere: the
    inline comments were rejected and the sticky only indexes titles.
    """
    reporter = _fresh_reporter()
    # The inline review batch is the only call routed through api_response.
    reporter.log.api_response = GitHubApiResponse(
        status=500,
        message="Server Error",
    )
    reporter.find_issue_comment.return_value = (77, "")

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
    )

    assert_that(posted).is_false()
    # Two updates: the healthy render, then the degraded re-render in place.
    assert_that(reporter.update_issue_comment.call_count).is_equal_to(2)
    reporter.post_issue_comment.assert_not_called()

    degraded = reporter.update_issue_comment.call_args.kwargs["body"]
    # Both the rejected finding and the one that maps to no diff line.
    assert_that(degraded).contains("2 findings could not be posted as inline")
    assert_that(degraded).contains("could not be posted")
    assert_that(degraded).contains("map to no line in this PR's diff")
    # The detail the inline comments would have carried is folded in.
    assert_that(degraded).contains("Unknown status grants access")
    assert_that(degraded).contains("No test for unknown status")
    # The round is not double-counted by the second render.
    assert_that(degraded.count("### Findings · Round 1")).is_equal_to(1)
    assert_that(degraded).does_not_contain(STATE_MARKER_PREFIX)


def test_unmappable_findings_are_folded_in_without_any_failure(
    sample_review_result: ReviewResult,
) -> None:
    """A finding that anchors to no diff line still gets its detail shown.

    It never had an inline comment to live on, so a title-only row in the open
    table would be the whole of it — the sticky would carry a verdict whose
    substance appears nowhere.
    """
    reporter = _fresh_reporter()

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
    )

    assert_that(posted).is_true()
    body = reporter.post_issue_comment.call_args.args[0]
    assert_that(body).contains("1 finding could not be posted as an inline comment")
    assert_that(body).contains("map to no line in this PR's diff")
    # Exactly the unmappable finding is folded in; the diff-mappable one keeps
    # its inline comment as the place its detail lives.
    assert_that(body).contains("📋 Details for 1 finding not posted inline")
    folded = body.split("📋 Details for", 1)[1].split("</details>", 1)[0]
    assert_that(folded).contains("No test for unknown status")
    assert_that(folded).does_not_contain("Unknown status grants access")


def test_failed_inline_post_never_posts_a_second_sticky(
    sample_review_result: ReviewResult,
) -> None:
    """A sticky that cannot be located is skipped, not duplicated on the PR."""
    reporter = _fresh_reporter()
    reporter.log.api_response = GitHubApiResponse(
        status=500,
        message="Server Error",
    )
    # No sticky exists beforehand, and the lookup after creation still misses.
    reporter.find_issue_comment.return_value = None

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
    )

    assert_that(posted).is_false()
    # Exactly one sticky was created, and no degraded duplicate followed it.
    assert_that(reporter.post_issue_comment.call_count).is_equal_to(1)
    reporter.update_issue_comment.assert_not_called()


def test_post_review_updates_existing_sticky(
    sample_review_result: ReviewResult,
) -> None:
    """An existing sticky comment is updated in place, not duplicated."""
    reporter = _fresh_reporter()
    prior_body = build_sticky_comment(
        request=StickyRequest(result=sample_review_result),
    )
    reporter.find_issue_comment.return_value = (42, prior_body)

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
    )

    assert_that(posted).is_true()
    reporter.update_issue_comment.assert_called_once()
    reporter.post_issue_comment.assert_not_called()
    reporter.delete_issue_comment.assert_not_called()
    kwargs = reporter.update_issue_comment.call_args.kwargs
    assert_that(kwargs["comment_id"]).is_equal_to(42)
    assert_that(kwargs["body"]).contains("## 🔎 Lintro Review —")


def test_refresh_uses_replacement_id_after_cross_actor_recreate(
    sample_review_result: ReviewResult,
) -> None:
    """A follow-up refresh must PATCH the recreated sticky, not the deleted id.

    When the App token cannot edit a leftover ``github-actions[bot]`` sticky,
    upsert deletes and recreates it. The refresh that persists inline-post
    failure details has to target the replacement id; the deleted id 404s.
    """
    reporter = _fresh_reporter()
    prior_body = build_sticky_comment(
        request=StickyRequest(result=sample_review_result),
    )
    reporter.find_issue_comment.side_effect = [
        (42, prior_body),
        (99, "replacement"),
    ]
    reporter.log.update_outcomes = [False, True]
    reporter.log.api_response = GitHubApiResponse(
        status=500,
        message="Server Error",
    )

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
    )

    assert_that(posted).is_false()
    reporter.delete_issue_comment.assert_called_once_with(comment_id=42)
    reporter.post_issue_comment.assert_called_once()
    refresh = reporter.update_issue_comment.call_args_list[1]
    assert_that(refresh.kwargs["comment_id"]).is_equal_to(99)
    assert_that(refresh.kwargs["body"]).contains(
        "could not be posted",
    )


def test_post_review_posts_inline_findings(
    sample_review_result: ReviewResult,
) -> None:
    """Diff-mappable findings are posted as inline review comments."""
    reporter = _fresh_reporter()

    posted = post_review_to_github(result=sample_review_result, reporter=reporter)

    assert_that(posted).is_true()
    # One finding maps to src/main.py:10 which is in the diff.
    assert_that(reporter.api_response.called).is_true()


def test_post_review_returns_false_when_unavailable(
    sample_review_result: ReviewResult,
) -> None:
    """Posting is skipped cleanly when GitHub context is unavailable."""
    reporter = MagicMock()
    reporter.is_available.return_value = False

    posted = post_review_to_github(result=sample_review_result, reporter=reporter)

    assert_that(posted).is_false()
    reporter.post_issue_comment.assert_not_called()


def test_post_error_comment_updates_sticky(
    sample_review_result: ReviewResult,
) -> None:
    """Error posting reuses and updates the sticky comment when present."""
    reporter = _fresh_reporter()
    reporter.find_issue_comment.return_value = (9, STICKY_MARKER)

    posted = post_review_error_to_github(
        error=AIAuthenticationError("bad key"),
        reporter=reporter,
    )

    assert_that(posted).is_true()
    reporter.update_issue_comment.assert_called_once()


def test_error_comment_preserves_prior_run_state() -> None:
    """A transient error re-emits prior run state so telemetry survives."""
    prior = ReviewState(
        runs=(
            RunRecord.from_dict(
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "model": "claude-sonnet-4-20250514",
                    "provider": "anthropic",
                    "total": 5000,
                    "cost": 0.02,
                    "estimated": False,
                    "depth": 1,
                    "p1": 0,
                    "p2": 1,
                    "p3": 0,
                },
            ),
        ),
    )
    body = format_error_comment(
        error=AIAuthenticationError("bad key"),
        prior_state=prior,
    )

    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)
    assert_that(body).contains("showing round 1 results below")
    assert_that(body).contains("## 🔎 Lintro Review")


def test_post_error_comment_recovers_prior_state(
    sample_review_result: ReviewResult,
) -> None:
    """post_review_error_to_github reloads prior runs and keeps their state."""
    from lintro.ai.review.review_state_codec import leftover_state_block
    from lintro.ai.review.sticky import advance_review_state

    reporter = _fresh_reporter()
    prior = advance_review_state(request=StickyRequest(result=sample_review_result))
    prior_body = f"{STICKY_MARKER}\n\nprior round{leftover_state_block(state=prior)}"
    reporter.find_issue_comment.return_value = (9, prior_body)

    post_review_error_to_github(
        error=AIRateLimitError("429"),
        reporter=reporter,
    )

    assert_that(reporter.log.issue_comment_bodies).is_not_empty()
    posted_body = reporter.log.issue_comment_bodies[-1]
    assert_that(posted_body).contains("showing round 1 results below")
    assert_that(posted_body).does_not_contain(STATE_MARKER_PREFIX)


# --- truncation safety for non-diff-mappable findings (#1099) ----------------


def _bulky_finding(
    *,
    severity: Severity,
    file: str,
    line: int,
    title: str,
    filler_repeats: int = 260,
) -> ReviewFinding:
    """Build a large finding whose rendered block eats into the comment cap."""
    filler = "detail " * filler_repeats
    return ReviewFinding(
        severity=severity,
        category="security",
        file=file,
        line=line,
        title=title,
        description=filler,
        cause=filler,
        fix=filler,
        confidence="high",
    )


def _result_with_findings(
    *,
    base: ReviewResult,
    findings: tuple[ReviewFinding, ...],
) -> ReviewResult:
    """Clone a review result with a replacement findings tuple."""
    return ReviewResult(
        metadata=base.metadata,
        summary=base.summary,
        checklist=base.checklist,
        findings=findings,
    )


def test_sticky_indexes_every_finding_and_stays_under_the_cap(
    sample_review_result: ReviewResult,
) -> None:
    """The v5 sticky indexes findings uniformly, whatever their inline fate.

    The old sticky embedded each finding's full detail, so it had to order
    non-diff-mappable findings first and truncate the rest. The v5 sticky
    carries one line per finding, so every finding fits and no ordering
    workaround is needed — detail lives on the inline comments instead.
    """
    diff_lines = {"src/mapped.py": set(range(1, 41))}
    mapped = tuple(
        _bulky_finding(
            severity=Severity.P1,
            file="src/mapped.py",
            line=index,
            title=f"MappedFinding{index}",
        )
        for index in range(1, 41)
    )
    fallback = _bulky_finding(
        severity=Severity.P3,
        file="src/unmapped.py",
        line=999,
        title="FallbackOnlyFinding",
    )
    result = _result_with_findings(
        base=sample_review_result,
        findings=(*mapped, fallback),
    )

    body = build_sticky_comment(
        request=StickyRequest(result=result, diff_lines=diff_lines),
    )

    assert_that(len(body)).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that(body).contains("### Findings ·")
    assert_that(body).contains("41 open")
    assert_that(body).contains("MappedFinding1")
    assert_that(
        "FallbackOnlyFinding" in body or "more open findings not listed" in body,
    ).is_true()


def test_sticky_state_round_trips_after_truncation(
    sample_review_result: ReviewResult,
) -> None:
    """The state block and verdict header survive a truncated body."""
    diff_lines = {"src/mapped.py": set(range(1, 41))}
    findings = tuple(
        _bulky_finding(
            severity=Severity.P1,
            file="src/mapped.py",
            line=index,
            title=f"MappedFinding{index}",
        )
        for index in range(1, 41)
    )
    result = _result_with_findings(
        base=sample_review_result,
        findings=findings,
    )

    body = build_sticky_comment(
        request=StickyRequest(result=result, diff_lines=diff_lines),
    )

    assert_that(body).contains("## 🔎 Lintro Review —")
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)


# --- final size safety net (#1909) -------------------------------------------


def test_cap_body_leaves_under_cap_body_unchanged() -> None:
    """Bodies under the cap pass through ``cap_body`` untouched."""
    body = f"{STICKY_MARKER}\n\n## 🔎 Lintro Review · round 1"

    capped = cap_body(body=body)

    assert_that(capped).is_equal_to(body)


def test_cap_body_truncates_visibly_as_a_last_resort() -> None:
    """Section-aware pruning handles real overflow; this is the backstop.

    ``fit_body`` sheds history, then resolved findings, then open findings —
    each with its own marker. ``cap_body`` only fires when a single
    unprunable section is itself over the cap, and even then the truncation
    must be announced rather than leaving a body that stops mid-sentence.
    """
    body = f"{STICKY_MARKER}\n\n" + "x" * (MAX_COMMENT_CHARS + 5_000)

    capped = cap_body(body=body)

    assert_that(len(capped)).is_less_than_or_equal_to(MAX_COMMENT_CHARS)
    assert_that(capped).contains("Comment truncated to fit GitHub's size limit")
    assert_that(capped).starts_with(STICKY_MARKER)


def test_build_sticky_survives_overflowing_finding_sets(
    sample_review_result: ReviewResult,
) -> None:
    """Integration: an over-cap finding set is trimmed explicitly, not silently."""
    findings = tuple(
        _bulky_finding(
            severity=Severity.P2,
            file=f"src/unmapped{index}.py",
            line=800 + index,
            title=f"StickyOverflow{index}",
        )
        for index in range(400)
    )
    result = _result_with_findings(base=sample_review_result, findings=findings)

    body = build_sticky_comment(request=StickyRequest(result=result, diff_lines=None))

    assert_that(len(body)).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that(body).contains("### Findings ·")
    assert_that(body).contains("400 open")
    assert_that(body).contains("StickyOverflow0")
    assert_that(body).contains("more open findings not listed")


# --- per-review comment body (#1910) ---------------------------------------


def test_post_review_uses_the_rich_review_body(
    sample_review_result: ReviewResult,
) -> None:
    """The review event carries the #1910 body, not the old bare label."""
    reporter = _fresh_reporter()
    reporter.fetch_pr_commit_shas.return_value = ["aaa111", "bbb222"]

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
        options=ReviewPostOptions(
            transport="cli",
            config_source="`.lintro-config.yaml`",
        ),
    )

    assert_that(posted).is_true()
    payload = reporter.log.api_calls[-1][2]
    assert_that(payload["body"]).contains("🔎 **Lintro review —")
    assert_that(payload["body"]).contains("**📊 Run stats**")
    assert_that(payload["body"]).contains("Config source: `.lintro-config.yaml`")
    assert_that(payload["body"]).does_not_contain("Lintro review findings")


def test_post_review_body_carries_the_fix_prompt_inline(
    sample_review_result: ReviewResult,
) -> None:
    """The posted body carries its own prompt, not a pointer to the sticky (#1956)."""
    reporter = _fresh_reporter()
    reporter.find_issue_comment.return_value = (
        42,
        build_sticky_comment(request=StickyRequest(result=sample_review_result)),
    )
    reporter.fetch_pr_commit_shas.return_value = []

    posted = post_review_to_github(result=sample_review_result, reporter=reporter)

    assert_that(posted).is_true()
    payload = reporter.log.api_calls[-1][2]
    assert_that(payload["body"]).contains("Fix prompt — this round's")
    assert_that(payload["body"]).contains("<details><summary>Show prompt</summary>")
    assert_that(payload["body"]).does_not_contain("identical to the")


# --- new-commit counting (#1910) -------------------------------------------


@pytest.mark.parametrize(
    ("prior_sha", "shas", "expected"),
    [
        ("aaa111", ["aaa111", "bbb222", "ccc333"], 2),
        ("ccc333", ["aaa111", "bbb222", "ccc333"], 0),
        ("aaa111abcdef", ["aaa111", "bbb222"], 1),
        ("zzz999", ["aaa111", "bbb222"], None),
        ("aaa111", [], None),
        ("", ["aaa111"], None),
    ],
)
def test_count_new_commits_measures_from_the_prior_head(
    prior_sha: str,
    shas: list[str],
    expected: int | None,
) -> None:
    """The count is commits after the prior head, or None when unresolvable."""
    reporter = _fresh_reporter()
    reporter.fetch_pr_commit_shas.return_value = shas
    prior_state = ReviewState(runs=(RunRecord(round=1, sha=prior_sha),))

    counted = _count_new_commits(reporter=reporter, prior_state=prior_state)

    assert_that(counted).is_equal_to(expected)


def test_count_new_commits_is_none_without_a_prior_round() -> None:
    """Round 1 has no baseline, so no delta is claimed."""
    reporter = _fresh_reporter()

    counted = _count_new_commits(reporter=reporter, prior_state=ReviewState())

    assert_that(counted).is_none()
    reporter.fetch_pr_commit_shas.assert_not_called()


def test_count_new_commits_is_none_when_the_listing_fails() -> None:
    """An unavailable commit listing yields None rather than a wrong count."""
    reporter = _fresh_reporter()
    reporter.fetch_pr_commit_shas.return_value = None
    prior_state = ReviewState(runs=(RunRecord(round=1, sha="aaa111"),))

    assert_that(
        _count_new_commits(reporter=reporter, prior_state=prior_state),
    ).is_none()


def test_review_body_and_degraded_sticky_coexist(
    sample_review_result: ReviewResult,
) -> None:
    """A rejected inline batch still folds detail into the sticky (#1909).

    The per-review body (#1910) is built inside the same branch that owns the
    degraded path, so a regression there would silently drop either the body or
    the fold-in.
    """
    reporter = _fresh_reporter()
    reporter.fetch_pr_commit_shas.return_value = []
    reporter.log.api_response = GitHubApiResponse(
        status=500,
        message="Server Error",
    )
    reporter.find_issue_comment.side_effect = [
        None,
        (77, build_sticky_comment(request=StickyRequest(result=sample_review_result))),
        (77, build_sticky_comment(request=StickyRequest(result=sample_review_result))),
    ]

    posted = post_review_to_github(result=sample_review_result, reporter=reporter)

    assert_that(posted).is_false()
    # The review body still reached the (rejected) review call…
    assert_that(reporter.api_response.call_args.args[2]["body"]).contains(
        "🔎 **Lintro review —",
    )
    # …and the sticky was re-rendered with the unpostable findings folded in.
    reporter.update_issue_comment.assert_called()
    folded = reporter.update_issue_comment.call_args.kwargs["body"]
    assert_that(folded).contains("could not be posted")
