"""Tests for the rich GitHub review posting adapter."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.github import (
    STATE_MARKER_PREFIX,
    STICKY_MARKER,
    build_sticky_comment,
    format_error_comment,
    format_finding_comment,
    format_review_summary,
    format_run_mechanics,
    parse_review_state,
    post_review_error_to_github,
    post_review_to_github,
    sanitize_comment_text,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult


def _fresh_reporter() -> MagicMock:
    """Build a MagicMock reporter with no existing sticky comment."""
    reporter = MagicMock()
    reporter.is_available.return_value = True
    reporter.find_issue_comment.return_value = None
    reporter.fetch_pr_diff_lines.return_value = {"src/main.py": {10}}
    reporter.post_issue_comment.return_value = True
    reporter.update_issue_comment.return_value = True
    reporter.api_request.return_value = True
    reporter.api_base = "https://api.github.com"
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


# --- formatting: severity badges, chips, collapsibles, suggestions ----------


def test_format_finding_comment_uses_color_badge_and_chips(
    sample_review_result: ReviewResult,
) -> None:
    """Finding comment renders color emoji severity and code chips."""
    finding = sample_review_result.findings[0]
    comment = format_finding_comment(finding=finding)

    assert_that(comment).contains("🔴 **P1**")
    assert_that(comment).contains("`security`")
    assert_that(comment).contains("`high confidence`")
    assert_that(comment).contains("<details><summary>")
    assert_that(comment).contains("Default to Expired")


def test_format_finding_comment_emits_suggestion_block(
    sample_review_result: ReviewResult,
) -> None:
    """A finding with suggested_code renders a GitHub suggestion block."""
    finding = replace(
        sample_review_result.findings[0],
        suggested_code="    return Status.EXPIRED",
    )
    comment = format_finding_comment(finding=finding)

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


def test_format_review_summary_has_counts_and_tldr(
    sample_review_result: ReviewResult,
) -> None:
    """Summary renders a severity count table and TL;DR."""
    summary = format_review_summary(result=sample_review_result)

    assert_that(summary).contains("## 🔎 Lintro Review")
    assert_that(summary).contains("| 🔴 P1 | 🟠 P2 | 🟡 P3 |")
    assert_that(summary).contains("**TL;DR**")
    assert_that(summary).contains("**Structured checks:** 3")


def test_format_review_summary_all_includes_appendix(
    sample_review_result: ReviewResult,
) -> None:
    """All mode appends cleared and orphan sections to the summary."""
    summary = format_review_summary(
        result=sample_review_result,
        checklist_display=ChecklistDisplay.ALL,
    )

    assert_that(summary).contains("### Cleared checks (1)")
    assert_that(summary).contains("Are access paths covered by tests?")
    assert_that(summary).contains("### Checklist concerns without findings (1)")


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
    comment = format_finding_comment(finding=finding)

    assert_that(comment).contains("```suggestion")
    assert_that(comment).does_not_contain("@team")


# --- sticky comment + cumulative aggregation --------------------------------


def test_build_sticky_comment_has_markers_and_cumulative(
    sample_review_result: ReviewResult,
) -> None:
    """First-run sticky comment carries markers and a cumulative header."""
    body = build_sticky_comment(result=sample_review_result)

    assert_that(body).contains(STICKY_MARKER)
    assert_that(body).contains(STATE_MARKER_PREFIX)
    assert_that(body).contains("**Cumulative (this PR):**")
    assert_that(body).contains("1 runs (1 exact, 0 est.)")


def test_build_sticky_comment_aggregates_prior_runs(
    sample_review_result: ReviewResult,
) -> None:
    """Cumulative header sums prior runs and flags mixed estimates."""
    prior = [
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
    ]
    body = build_sticky_comment(result=sample_review_result, prior_runs=prior)

    assert_that(body).contains("2 runs (1 exact, 1 est.)")
    # Mixed estimate => cumulative flagged approximate.
    assert_that(body).contains("~$")
    assert_that(body).contains("Previous runs (1)")
    assert_that(body).contains("`cursor:auto` ×1")


def test_round_trip_state_parsing(sample_review_result: ReviewResult) -> None:
    """State written into a sticky comment parses back to run records."""
    body = build_sticky_comment(result=sample_review_result)
    runs = parse_review_state(body=body)

    assert_that(runs).is_length(1)
    assert_that(runs[0]["model"]).is_equal_to("claude-sonnet-4-20250514")


def test_parse_review_state_handles_missing_block() -> None:
    """A body with no state block yields an empty run list."""
    assert_that(parse_review_state(body="no state here")).is_empty()


# --- partial state ----------------------------------------------------------


def test_summary_renders_partial_state(
    sample_review_result: ReviewResult,
) -> None:
    """A partial review renders an explicit partial note."""
    metadata = replace(
        sample_review_result.metadata,
        partial=True,
        stopped_reason="cost cap",
        chunks_reviewed=2,
        chunks_total=5,
    )
    result = ReviewResult(
        metadata=metadata,
        summary=sample_review_result.summary,
        checklist=sample_review_result.checklist,
        findings=sample_review_result.findings,
    )
    summary = format_review_summary(result=result)

    assert_that(summary).contains("Partial review")
    assert_that(summary).contains("cost cap")
    assert_that(summary).contains("2 of 5 chunks")


def test_summary_renders_partial_state_before_any_chunk(
    sample_review_result: ReviewResult,
) -> None:
    """A cost cap tripping before any chunk renders an actionable note."""
    metadata = replace(
        sample_review_result.metadata,
        partial=True,
        stopped_reason="cost cap ($0.50) reached",
        chunks_reviewed=0,
        chunks_total=4,
    )
    result = ReviewResult(
        metadata=metadata,
        summary=sample_review_result.summary,
        checklist=sample_review_result.checklist,
        findings=(),
    )
    summary = format_review_summary(result=result)

    assert_that(summary).contains("Partial review")
    assert_that(summary).contains("before reviewing any of 4 chunks")
    assert_that(summary).contains("ai.max_cost_usd")


# --- posting: create, update, inline ----------------------------------------


def test_post_review_creates_sticky_when_absent(
    sample_review_result: ReviewResult,
) -> None:
    """With no existing comment, a new sticky comment is posted."""
    reporter = _fresh_reporter()

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
    )

    assert_that(posted).is_true()
    reporter.post_issue_comment.assert_called_once()
    reporter.update_issue_comment.assert_not_called()
    body = reporter.post_issue_comment.call_args.args[0]
    assert_that(body).contains(STICKY_MARKER)


def test_post_review_updates_existing_sticky(
    sample_review_result: ReviewResult,
) -> None:
    """An existing sticky comment is updated in place, not duplicated."""
    reporter = _fresh_reporter()
    prior_body = build_sticky_comment(result=sample_review_result)
    reporter.find_issue_comment.return_value = (42, prior_body)

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
    )

    assert_that(posted).is_true()
    reporter.update_issue_comment.assert_called_once()
    reporter.post_issue_comment.assert_not_called()
    kwargs = reporter.update_issue_comment.call_args.kwargs
    assert_that(kwargs["comment_id"]).is_equal_to(42)
    assert_that(kwargs["body"]).contains("2 runs")


def test_post_review_posts_inline_findings(
    sample_review_result: ReviewResult,
) -> None:
    """Diff-mappable findings are posted as inline review comments."""
    reporter = _fresh_reporter()

    posted = post_review_to_github(result=sample_review_result, reporter=reporter)

    assert_that(posted).is_true()
    # One finding maps to src/main.py:10 which is in the diff.
    assert_that(reporter.api_request.called).is_true()


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
    prior = [
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
    ]
    body = format_error_comment(
        error=AIAuthenticationError("bad key"),
        prior_runs=prior,
    )

    assert_that(body).contains(STATE_MARKER_PREFIX)
    recovered = parse_review_state(body=body)
    assert_that(recovered).is_length(1)
    assert_that(recovered[0]["total"]).is_equal_to(5000)


def test_post_error_comment_recovers_prior_state(
    sample_review_result: ReviewResult,
) -> None:
    """post_review_error_to_github reloads prior runs and keeps their state."""
    reporter = _fresh_reporter()
    prior_body = build_sticky_comment(result=sample_review_result)
    reporter.find_issue_comment.return_value = (9, prior_body)

    post_review_error_to_github(
        error=AIRateLimitError("429"),
        reporter=reporter,
    )

    posted_body = reporter.update_issue_comment.call_args.kwargs["body"]
    assert_that(parse_review_state(body=posted_body)).is_length(1)
