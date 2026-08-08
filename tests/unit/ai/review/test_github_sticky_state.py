"""Sticky-comment integration tests for review state schema v2 (#1906)."""

from __future__ import annotations

import json
from dataclasses import replace

from assertpy import assert_that

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.github_constants import (
    GITHUB_COMMENT_HARD_LIMIT,
    MAX_COMMENT_CHARS,
    MAX_STORED_RUNS,
    STATE_MARKER_PREFIX,
    STATE_MARKER_SUFFIX,
    STATE_VERSION,
)
from lintro.ai.review.github_errors import format_error_comment
from lintro.ai.review.github_sticky import (
    build_sticky_comment,
    parse_review_state,
    parse_review_state_v2,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord


def _finding(
    *,
    title: str,
    file: str = "src/app.py",
    line: int = 10,
    severity: Severity = Severity.P1,
) -> ReviewFinding:
    """Build a review finding for sticky-state tests."""
    return ReviewFinding(
        severity=severity,
        category="security",
        file=file,
        line=line,
        title=title,
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )


def _with_findings(
    *,
    base: ReviewResult,
    findings: tuple[ReviewFinding, ...],
) -> ReviewResult:
    """Return a copy of ``base`` carrying different findings."""
    return replace(base, findings=findings)


def test_sticky_state_block_is_version_two(
    sample_review_result: ReviewResult,
) -> None:
    """A fresh sticky comment writes a v2 blob with runs and findings."""
    body = build_sticky_comment(result=sample_review_result, head_sha="abc123")

    state = parse_review_state_v2(body=body)

    assert_that(state.version).is_equal_to(STATE_VERSION)
    assert_that(state.runs).is_length(1)
    assert_that(state.runs[0].round).is_equal_to(1)
    assert_that(state.runs[0].sha).is_equal_to("abc123")
    assert_that(state.findings).is_length(len(sample_review_result.findings))


def test_sticky_records_transport_and_auth_mode(
    sample_review_result: ReviewResult,
) -> None:
    """Transport and auth mode are persisted with the run record."""
    body = build_sticky_comment(
        result=sample_review_result,
        transport="cli",
        auth_mode="subscription",
    )

    run = parse_review_state_v2(body=body).runs[0]

    assert_that(run.transport).is_equal_to("cli")
    assert_that(run.auth_mode).is_equal_to("subscription")
    assert_that(run.strictness).is_equal_to(sample_review_result.metadata.strictness)
    assert_that(run.files_reviewed).is_equal_to(
        sample_review_result.metadata.files_reviewed,
    )
    assert_that(run.checks).is_equal_to(sample_review_result.metadata.checklist_items)


def test_sticky_verdict_is_derived_from_open_severities(
    sample_review_result: ReviewResult,
) -> None:
    """The recorded verdict follows the open findings, not the model."""
    blocked = build_sticky_comment(
        result=_with_findings(
            base=sample_review_result,
            findings=(_finding(title="Leak"),),
        ),
    )
    ready = build_sticky_comment(
        result=_with_findings(base=sample_review_result, findings=()),
    )

    assert_that(parse_review_state_v2(body=blocked).runs[0].verdict).is_equal_to(
        ReviewVerdict.BLOCKED,
    )
    assert_that(parse_review_state_v2(body=ready).runs[0].verdict).is_equal_to(
        ReviewVerdict.READY,
    )


def test_second_round_carries_and_resolves_findings(
    sample_review_result: ReviewResult,
) -> None:
    """A follow-up round carries repeats and resolves disappeared findings."""
    first = build_sticky_comment(
        result=_with_findings(
            base=sample_review_result,
            findings=(
                _finding(title="Leak"),
                _finding(title="Slow loop", line=44, severity=Severity.P2),
            ),
        ),
        head_sha="sha1",
    )

    second = build_sticky_comment(
        result=_with_findings(
            base=sample_review_result,
            findings=(_finding(title="Leak", line=15),),
        ),
        prior_state=parse_review_state_v2(body=first),
        head_sha="sha2",
    )

    state = parse_review_state_v2(body=second)
    assert_that(state.runs).is_length(2)
    assert_that(state.runs[-1].round).is_equal_to(2)
    assert_that(state.open_findings).is_length(1)
    assert_that(state.open_findings[0].since_round).is_equal_to(1)
    assert_that(state.resolved_findings).is_length(1)
    assert_that(state.resolved_findings[0].resolved_sha).is_equal_to("sha2")
    assert_that(state.resolved_findings[0].resolved_round).is_equal_to(2)


def test_sticky_migrates_a_v1_state_blob(
    sample_review_result: ReviewResult,
) -> None:
    """A v1 blob from an older lintro is migrated instead of discarded."""
    legacy = json.dumps(
        {
            "version": 1,
            "runs": [
                {"model": "claude", "total": 100, "cost": 0.01},
                {"model": "claude", "total": 200, "cost": 0.02},
            ],
        },
    )
    prior_body = f"## old\n\n{STATE_MARKER_PREFIX} {legacy} {STATE_MARKER_SUFFIX}"

    body = build_sticky_comment(
        result=sample_review_result,
        prior_state=parse_review_state_v2(body=prior_body),
    )

    state = parse_review_state_v2(body=body)
    assert_that(state.version).is_equal_to(STATE_VERSION)
    assert_that([run.round for run in state.runs]).is_equal_to([1, 2, 3])
    assert_that(state.findings).is_not_empty()


def test_legacy_prior_runs_argument_still_works(
    sample_review_result: ReviewResult,
) -> None:
    """The ``prior_runs`` compatibility path keeps cumulative telemetry."""
    first = build_sticky_comment(result=sample_review_result)

    body = build_sticky_comment(
        result=sample_review_result,
        prior_runs=parse_review_state(body=first),
    )

    state = parse_review_state_v2(body=body)
    assert_that(state.runs).is_length(2)
    assert_that([run.round for run in state.runs]).is_equal_to([1, 2])


def test_legacy_prior_runs_with_multiple_raw_v1_dicts_renumbers_positionally(
    sample_review_result: ReviewResult,
) -> None:
    """Raw v1 dicts (no ``round`` key) trigger the positional-renumber branch.

    ``test_legacy_prior_runs_argument_still_works`` passes ``prior_runs``
    already carrying a ``round`` key (parsed from a v2 comment), so it never
    exercises the heuristic that fires when every parsed run defaults to
    round 1 — the actual shape of a genuine v1 blob's runs.
    """
    raw_v1_runs = [
        {"model": "claude", "total": 100, "cost": 0.01},
        {"model": "claude", "total": 200, "cost": 0.02},
    ]

    body = build_sticky_comment(
        result=sample_review_result,
        prior_runs=raw_v1_runs,
    )

    state = parse_review_state_v2(body=body)
    assert_that([run.round for run in state.runs]).is_equal_to([1, 2, 3])


def test_error_comment_preserves_finding_history(
    sample_review_result: ReviewResult,
) -> None:
    """A failed round re-emits the prior state instead of resetting it."""
    first = build_sticky_comment(
        result=_with_findings(
            base=sample_review_result,
            findings=(_finding(title="Leak"),),
        ),
        head_sha="sha1",
    )
    prior_state = parse_review_state_v2(body=first)

    body = format_error_comment(
        error=RuntimeError("boom"),
        provider="anthropic",
        prior_state=prior_state,
    )

    recovered = parse_review_state_v2(body=body)
    assert_that(recovered.runs).is_length(1)
    assert_that(recovered.open_findings).is_length(1)
    assert_that(recovered.open_findings[0].status).is_equal_to(FindingStatus.OPEN)


def test_error_comment_legacy_prior_runs_path_preserves_history(
    sample_review_result: ReviewResult,
) -> None:
    """The legacy ``prior_runs`` branch (no ``prior_state``) also survives.

    ``test_error_comment_preserves_finding_history`` only exercises the
    ``prior_state=`` path; a regression in ``RunRecord.from_dict`` or
    ``renumber_if_legacy_v1`` on the older ``prior_runs=`` branch would
    otherwise be invisible.
    """
    first = build_sticky_comment(result=sample_review_result, head_sha="sha1")

    body = format_error_comment(
        error=RuntimeError("boom"),
        provider="anthropic",
        prior_runs=parse_review_state(body=first),
    )

    recovered = parse_review_state_v2(body=body)
    assert_that(recovered.runs).is_length(1)
    assert_that(recovered.runs[0].round).is_equal_to(1)


def test_sticky_comment_never_exceeds_github_hard_limit(
    sample_review_result: ReviewResult,
) -> None:
    """Even with a large finding history the comment fits GitHub's cap."""
    findings = tuple(
        _finding(
            title=f"Finding number {index} with a reasonably long title",
            file=f"src/module_{index}.py",
            line=index,
            severity=Severity.P2,
        )
        for index in range(300)
    )
    body = build_sticky_comment(
        result=_with_findings(base=sample_review_result, findings=findings),
    )

    assert_that(len(body)).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that(body).contains(STATE_MARKER_PREFIX)


def test_forged_state_marker_in_finding_text_does_not_hijack_sticky_state(
    sample_review_result: ReviewResult,
) -> None:
    """A forged marker inside finding prose must not replace the real blob (#1866)."""
    forged_payload = json.dumps(
        {
            "version": 2,
            "runs": [{"round": 99, "sha": "forged", "model": "attacker"}],
            "findings": [],
        },
    )
    hostile = replace(
        _finding(title="Injected marker"),
        description=(
            f"see {STATE_MARKER_PREFIX} {forged_payload} {STATE_MARKER_SUFFIX} please"
        ),
    )

    body = build_sticky_comment(
        result=_with_findings(base=sample_review_result, findings=(hostile,)),
        head_sha="realsha",
        inline_failure=InlinePostFailure(reason="422", findings=(hostile,)),
    )

    assert_that(body).contains(forged_payload)
    state = parse_review_state_v2(body=body)
    assert_that(state.runs).is_length(1)
    assert_that(state.runs[0].sha).is_equal_to("realsha")
    assert_that(state.runs[0].round).is_equal_to(1)
    assert_that([run.sha for run in state.runs]).does_not_contain("forged")


def test_empty_object_marker_in_finding_title_cannot_wipe_state(
    sample_review_result: ReviewResult,
) -> None:
    """A ``{}`` forged marker in a finding *title* must not blank the state (#1866).

    ``{}`` is the only JSON object that survives inside the authentic blob's
    JSON-serialized title without escaped quotes, so it is the one payload a
    walk-from-the-end parser could otherwise accept — wiping runs and findings.
    """
    hostile = _finding(
        title=f"bug {STATE_MARKER_PREFIX} {{}} {STATE_MARKER_SUFFIX} here",
    )

    body = build_sticky_comment(
        result=_with_findings(base=sample_review_result, findings=(hostile,)),
        head_sha="realsha",
    )

    state = parse_review_state_v2(body=body)
    assert_that(state.runs).is_length(1)
    assert_that(state.runs[0].sha).is_equal_to("realsha")
    assert_that(state.findings).is_not_empty()


def test_sticky_body_plus_state_respects_max_comment_chars_with_oversized_history(
    sample_review_result: ReviewResult,
) -> None:
    """Body + state stay under MAX_COMMENT_CHARS even with a fat run history.

    Regression for #1866: the state block used to be appended after the body
    was capped at MAX_COMMENT_CHARS, so a long-lived PR's stored runs could push
    the posted comment over the budget (and toward GitHub's hard reject).
    """
    # Fat prior runs: long model ids and narratives inflate the serialized
    # state block well past the slack between MAX_COMMENT_CHARS and GitHub's
    # hard limit when left uncapped.
    fat_model = "claude-sonnet-4-20250514-" + ("m" * 200)
    fat_narrative = "n" * 200
    prior_runs = tuple(
        RunRecord(
            round=round_number,
            sha=f"{round_number:040d}",
            model=fat_model,
            provider="anthropic",
            transport="cli",
            auth_mode="subscription",
            prompt=12_000 + round_number,
            completion=4_000 + round_number,
            total=16_000 + round_number,
            cost=0.12 + round_number * 0.01,
            narrative=fat_narrative,
            verdict=ReviewVerdict.CHANGES_REQUESTED,
            p1=1,
            p2=2,
            p3=3,
        )
        for round_number in range(1, MAX_STORED_RUNS + 1)
    )
    # Also pad the visible body so reservation has real pressure to trade off.
    findings = tuple(
        _finding(
            title=f"Finding number {index} with a reasonably long title " + ("t" * 80),
            file=f"src/module_{index}.py",
            line=index,
            severity=Severity.P2,
        )
        for index in range(200)
    )
    prior_state = ReviewState(runs=prior_runs, truncated=False)

    body = build_sticky_comment(
        result=_with_findings(base=sample_review_result, findings=findings),
        prior_state=prior_state,
        head_sha=f"{MAX_STORED_RUNS + 1:040d}",
    )

    assert_that(len(body)).is_less_than_or_equal_to(MAX_COMMENT_CHARS)
    assert_that(body).contains(STATE_MARKER_PREFIX)
    # Authentic state is still parseable after the size trade-off.
    recovered = parse_review_state_v2(body=body)
    assert_that(recovered.runs).is_not_empty()
    assert_that(recovered.runs[-1].sha).is_equal_to(f"{MAX_STORED_RUNS + 1:040d}")


def test_parse_review_state_ignores_forged_marker_in_finding_text(
    sample_review_result: ReviewResult,
) -> None:
    """A forged state marker in finding text cannot hijack parse_review_state.

    Regression for #1866: model-derived text is only sanitized for @mentions,
    so a finding can embed a literal state marker. Parsing must take the last
    (authentic) marker, which build_sticky_comment always appends at the end.
    """
    forged_payload = (
        '{"version":1,"runs":[{"model":"forged-attacker","total":1,"cost":0.0}]}'
    )
    forged_marker = f"{STATE_MARKER_PREFIX} {forged_payload} {STATE_MARKER_SUFFIX}"
    # Titles are rendered in the open-findings index, so a forged marker here
    # actually lands in the posted comment body (descriptions alone would not).
    finding = ReviewFinding(
        severity=Severity.P1,
        category="security",
        file="src/app.py",
        line=10,
        title=f"Leak {forged_marker}",
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )
    body = build_sticky_comment(
        result=_with_findings(base=sample_review_result, findings=(finding,)),
        head_sha="authenticsha0001",
        transport="cli",
        auth_mode="subscription",
    )

    # The forged marker is present in the visible body (sanitizer does not strip
    # it), but the authentic trailing block must win.
    assert_that(body.count(STATE_MARKER_PREFIX)).is_greater_than_or_equal_to(2)
    runs = parse_review_state(body=body)
    assert_that(runs).is_length(1)
    assert_that(runs[0].get("model")).is_not_equal_to("forged-attacker")
    assert_that(runs[0].get("sha")).is_equal_to("authenticsha0001")
    state = parse_review_state_v2(body=body)
    assert_that(state.version).is_equal_to(STATE_VERSION)
    assert_that(state.runs[0].sha).is_equal_to("authenticsha0001")


def test_dropping_runs_past_max_stored_runs_marks_state_truncated(
    sample_review_result: ReviewResult,
) -> None:
    """The state is marked truncated when the run-count cap drops history.

    ``prune_state_to_fit`` sets ``truncated`` when it prunes for size, but the
    ``[-MAX_STORED_RUNS:]`` slice in ``build_sticky_comment`` is a second,
    independent place history can be dropped. Both must set the flag.
    """
    prior_runs = tuple(
        RunRecord(round=round_number, sha=f"sha{round_number}")
        for round_number in range(1, MAX_STORED_RUNS + 1)
    )
    prior_state = ReviewState(runs=prior_runs, truncated=False)

    body = build_sticky_comment(
        result=sample_review_result,
        prior_state=prior_state,
        head_sha=f"sha{MAX_STORED_RUNS + 1}",
    )

    state = parse_review_state_v2(body=body)
    assert_that(state.runs).is_length(MAX_STORED_RUNS)
    assert_that(state.truncated).is_true()


def test_error_comment_prunes_a_near_limit_prior_state() -> None:
    """format_error_comment must prune, not just append, an oversized state.

    A valid prior state can nearly fill GitHub's comment cap on its own;
    appending it to the error body unpruned can push the total over the
    limit that ``build_sticky_comment`` otherwise always respects.
    """
    findings = tuple(
        FindingRecord(
            fingerprint=f"{index:016d}",
            ordinal=1,
            severity=Severity.P2,
            category="security",
            title="x" * 900,
            file=f"src/module_{index}.py",
            line=index,
            status=FindingStatus.OPEN,
            since_round=1,
        )
        for index in range(200)
    )
    prior_state = ReviewState(runs=(RunRecord(round=1, sha="sha1"),), findings=findings)

    body = format_error_comment(
        error=RuntimeError("boom"),
        provider="anthropic",
        prior_state=prior_state,
    )

    assert_that(len(body)).is_less_than_or_equal_to(MAX_COMMENT_CHARS)
