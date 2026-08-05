"""Tests for the rich GitHub review posting adapter."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.github import (
    GITHUB_COMMENT_HARD_LIMIT,
    MAX_COMMENT_CHARS,
    STATE_MARKER_PREFIX,
    STICKY_MARKER,
    _cap_body,
    _count_new_commits,
    _format_findings_section,
    _sticky_comment_id,
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
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord


def _fresh_reporter() -> MagicMock:
    """Build a MagicMock reporter with no existing sticky comment."""
    reporter = MagicMock()
    reporter.is_available.return_value = True
    reporter.find_issue_comment.return_value = None
    reporter.fetch_pr_diff_lines.return_value = {"src/main.py": {10}}
    reporter.fetch_pr_commit_shas.return_value = []
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


def test_build_sticky_comment_has_markers_and_verdict_header(
    sample_review_result: ReviewResult,
) -> None:
    """First-run sticky comment leads with the round and the derived verdict."""
    body = build_sticky_comment(result=sample_review_result)

    assert_that(body).contains(STICKY_MARKER)
    assert_that(body).contains(STATE_MARKER_PREFIX)
    assert_that(body).contains("## 🔎 Lintro Review · round 1")
    assert_that(body).contains("**⛔ Blocked** — 1 open blocker")
    # Accounting never precedes the verdict (#1905).
    assert_that(body.index("Lintro Review")).is_less_than(body.index("est. cost"))


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

    assert_that(body).contains("🕘 Run history — 2 runs")
    # Mixed estimate => cumulative flagged approximate.
    assert_that(body).contains("~$")
    assert_that(body).contains("`cursor:auto` ×1")
    # History lives in exactly one collapsible.
    assert_that(body.count("🕘 Run history")).is_equal_to(1)


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


def test_failed_inline_post_folds_details_into_the_sticky(
    sample_review_result: ReviewResult,
) -> None:
    """A rejected inline batch leaves the sticky as the findings' only surface.

    Without this the PR keeps a verdict whose findings appear nowhere: the
    inline comments were rejected and the sticky only indexes titles.
    """
    reporter = _fresh_reporter()
    # The inline review batch is the only call routed through api_request.
    reporter.api_request.return_value = False
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
    assert_that(parse_review_state(body=degraded)).is_length(1)


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
    reporter.api_request.return_value = False
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


def test_sticky_comment_id_short_circuits_on_a_known_id() -> None:
    """A known id is reused without a second lookup against the API."""
    reporter = _fresh_reporter()

    assert_that(_sticky_comment_id(reporter=reporter, known=42)).is_equal_to(42)
    reporter.find_issue_comment.assert_not_called()


def test_sticky_comment_id_relocates_a_just_created_comment() -> None:
    """With no prior id the sticky is re-located by its marker."""
    reporter = _fresh_reporter()
    reporter.find_issue_comment.return_value = (99, "body")

    assert_that(_sticky_comment_id(reporter=reporter, known=None)).is_equal_to(99)


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


def test_findings_section_orders_fallback_before_diff_mappable(
    sample_review_result: ReviewResult,
) -> None:
    """Non-diff-mappable findings render ahead of diff-mappable ones."""
    diff_lines = {"src/mapped.py": {10}}
    mapped = ReviewFinding(
        severity=Severity.P1,
        category="security",
        file="src/mapped.py",
        line=10,
        title="MappedInlineFinding",
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )
    fallback = ReviewFinding(
        severity=Severity.P3,
        category="style",
        file="src/unmapped.py",
        line=999,
        title="FallbackOnlyFinding",
        description="d",
        cause="c",
        fix="f",
        confidence="low",
    )
    result = _result_with_findings(
        base=sample_review_result,
        findings=(mapped, fallback),
    )

    summary = format_review_summary(result=result, diff_lines=diff_lines)

    fallback_at = summary.find("FallbackOnlyFinding")
    mapped_at = summary.find("MappedInlineFinding")
    assert_that(fallback_at).is_greater_than(-1)
    # Fallback (P3) precedes the diff-mappable P1 despite lower severity.
    assert_that(fallback_at).is_less_than(mapped_at)


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

    # The old detail-embedding renderer would blow past the cap on this input.
    unbudgeted = format_review_summary(result=result, diff_lines=diff_lines)
    assert_that(len(unbudgeted)).is_greater_than(MAX_COMMENT_CHARS)

    body = build_sticky_comment(result=result, diff_lines=diff_lines)

    assert_that(len(body)).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that(body).contains("### Open findings (41)")
    assert_that(body).contains("FallbackOnlyFinding")
    assert_that(body).contains("MappedFinding1")


def test_all_fallback_overflow_truncates_to_logs_not_inline() -> None:
    """All-fallback overflow drops via an explicit *workflow-logs* marker.

    When ``diff_lines`` is ``None`` every finding is fallback (no inline
    surface). If they exceed GitHub's hard comment limit, truncation is
    unavoidable — but it must be explicit and must NOT point readers to inline
    comments that do not exist for fallback findings.
    """
    findings = tuple(
        _bulky_finding(
            severity=Severity.P2,
            file=f"src/unmapped{index}.py",
            line=900 + index,
            title=f"FallbackFinding{index}",
        )
        for index in range(5)
    )

    lines = _format_findings_section(
        findings=findings,
        checklist_display=ChecklistDisplay.OFF,
        question_map={},
        diff_lines=None,
        char_budget=200,  # forces overflow so the marker path is exercised
    )
    body = "\n".join(lines)

    # At least the first fallback finding is always rendered.
    assert_that(body).contains("FallbackFinding0")
    # Overflow is explicit, and points at the logs — never at (nonexistent)
    # inline comments for fallback findings.
    assert_that(body).contains("more finding(s) truncated")
    assert_that(body).contains("workflow logs")
    assert_that(body).does_not_contain("inline comments")


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

    body = build_sticky_comment(result=result, diff_lines=diff_lines)

    assert_that(body).contains("## 🔎 Lintro Review · round 1")
    assert_that(body).contains(STATE_MARKER_PREFIX)
    runs = parse_review_state(body=body)
    assert_that(runs).is_length(1)
    assert_that(runs[0]["model"]).is_equal_to("claude-sonnet-4-20250514")


# --- final size safety net (#1909) -------------------------------------------


def test_cap_body_leaves_under_cap_body_unchanged() -> None:
    """Bodies under the cap pass through _cap_body untouched."""
    body = f"{STICKY_MARKER}\n\n## 🔎 Lintro Review · round 1"

    capped = _cap_body(body=body)

    assert_that(capped).is_equal_to(body)


def test_cap_body_truncates_visibly_as_a_last_resort() -> None:
    """Section-aware pruning handles real overflow; this is the backstop.

    ``_fit_body`` sheds history, then resolved findings, then open findings —
    each with its own marker. ``_cap_body`` only fires when a single
    unprunable section is itself over the cap, and even then the truncation
    must be announced rather than leaving a body that stops mid-sentence.
    """
    body = f"{STICKY_MARKER}\n\n" + "x" * (MAX_COMMENT_CHARS + 5_000)

    capped = _cap_body(body=body)

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

    body = build_sticky_comment(result=result, diff_lines=None)

    assert_that(len(body)).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that(body).contains("### Open findings (400)")
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
        transport="cli",
        config_source="`.lintro-config.yaml`",
    )

    assert_that(posted).is_true()
    payload = reporter.api_request.call_args.args[2]
    assert_that(payload["body"]).contains("🔎 **Lintro review —")
    assert_that(payload["body"]).contains("**📊 Run stats**")
    assert_that(payload["body"]).contains("Config source: `.lintro-config.yaml`")
    assert_that(payload["body"]).does_not_contain("Lintro review findings")


def test_post_review_body_links_the_pointer_to_an_existing_sticky(
    sample_review_result: ReviewResult,
) -> None:
    """When the sticky already exists, the dedup pointer links straight to it."""
    reporter = _fresh_reporter()
    reporter.find_issue_comment.return_value = (
        42,
        build_sticky_comment(result=sample_review_result),
    )
    reporter.fetch_pr_commit_shas.return_value = []

    post_review_to_github(result=sample_review_result, reporter=reporter)

    payload = reporter.api_request.call_args.args[2]
    assert_that(payload["body"]).contains(
        "https://github.com/owner/name/pull/7#issuecomment-42",
    )


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


def test_review_body_links_a_sticky_created_in_this_same_run(
    sample_review_result: ReviewResult,
) -> None:
    """Round 1's pointer resolves the sticky that was just created (#1909/#1910).

    The sticky is upserted before the inline review is posted, so by the time
    the body is built the comment exists even on the first round — the pointer
    must link to it rather than fall back to unlinked text.
    """
    reporter = _fresh_reporter()
    reporter.fetch_pr_commit_shas.return_value = []
    # First lookup loads prior state (no sticky yet); the second runs after the
    # sticky has been created and finds it.
    reporter.find_issue_comment.side_effect = [
        None,
        (99, build_sticky_comment(result=sample_review_result)),
    ]

    post_review_to_github(result=sample_review_result, reporter=reporter)

    payload = reporter.api_request.call_args.args[2]
    assert_that(payload["body"]).contains(
        "https://github.com/owner/name/pull/7#issuecomment-99",
    )


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
    reporter.api_request.return_value = False
    reporter.find_issue_comment.side_effect = [
        None,
        (77, build_sticky_comment(result=sample_review_result)),
        (77, build_sticky_comment(result=sample_review_result)),
    ]

    posted = post_review_to_github(result=sample_review_result, reporter=reporter)

    assert_that(posted).is_false()
    # The review body still reached the (rejected) review call…
    assert_that(reporter.api_request.call_args.args[2]["body"]).contains(
        "🔎 **Lintro review —",
    )
    # …and the sticky was re-rendered with the unpostable findings folded in.
    reporter.update_issue_comment.assert_called()
    folded = reporter.update_issue_comment.call_args.kwargs["body"]
    assert_that(folded).contains("could not be posted")
