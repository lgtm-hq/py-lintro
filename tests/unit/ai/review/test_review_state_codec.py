"""Tests for the versioned review-state blob codec (#1906)."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.github_constants import (
    GITHUB_COMMENT_HARD_LIMIT,
    STATE_MARKER_PREFIX,
    STATE_MARKER_SUFFIX,
    STATE_VERSION,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.review_state_codec import (
    decode_state,
    encode_state,
    prune_state_to_fit,
    render_state_block,
)


def _wrap(payload: dict[str, object]) -> str:
    """Wrap a state payload in the hidden comment markers.

    Args:
        payload: State mapping to embed.

    Returns:
        A sticky comment body carrying the payload.
    """
    return (
        "## Review\n\n"
        f"{STATE_MARKER_PREFIX} {json.dumps(payload)} {STATE_MARKER_SUFFIX}"
    )


def _record(*, fingerprint: str, since_round: int = 1) -> FindingRecord:
    """Build a finding record for codec tests."""
    return FindingRecord(
        fingerprint=fingerprint,
        ordinal=1,
        severity=Severity.P2,
        category="security",
        title="Hardcoded credential",
        file="src/app.py",
        line=12,
        status=FindingStatus.OPEN,
        since_round=since_round,
    )


def test_round_trip_preserves_runs_and_findings() -> None:
    """Encoding then decoding a v2 state preserves both record kinds."""
    state = ReviewState(
        runs=(
            RunRecord(
                round=1,
                sha="abc",
                model="claude",
                transport="cli",
                auth_mode="subscription",
                verdict=ReviewVerdict.CHANGES_REQUESTED,
                total=1200,
                cost=0.05,
            ),
        ),
        findings=(_record(fingerprint="a" * 16),),
    )

    decoded = decode_state(body=f"body {render_state_block(state=state)}")

    assert_that(decoded.version).is_equal_to(STATE_VERSION)
    assert_that(decoded.runs).is_length(1)
    assert_that(decoded.runs[0].transport).is_equal_to("cli")
    assert_that(decoded.runs[0].auth_mode).is_equal_to("subscription")
    assert_that(decoded.runs[0].verdict).is_equal_to(ReviewVerdict.CHANGES_REQUESTED)
    assert_that(decoded.findings).is_length(1)
    assert_that(decoded.findings[0].fingerprint).is_equal_to("a" * 16)


def test_resolved_provenance_round_trips() -> None:
    """``resolved_in`` survives a serialize/parse cycle."""
    resolved = FindingRecord(
        fingerprint="b" * 16,
        status=FindingStatus.RESOLVED,
        since_round=1,
        resolved_sha="deadbeef",
        resolved_round=3,
        inline_comment_id=99,
        regressed=True,
    )

    decoded = decode_state(
        body=render_state_block(state=ReviewState(findings=(resolved,))),
    )

    record = decoded.findings[0]
    assert_that(record.status).is_equal_to(FindingStatus.RESOLVED)
    assert_that(record.resolved_sha).is_equal_to("deadbeef")
    assert_that(record.resolved_round).is_equal_to(3)
    assert_that(record.inline_comment_id).is_equal_to(99)
    assert_that(record.regressed).is_true()


def test_v1_blob_migrates_with_sequential_rounds() -> None:
    """A v1 blob decodes as v2 with positional round numbers and no findings."""
    body = _wrap(
        {
            "version": 1,
            "runs": [
                {"model": "claude", "total": 100, "cost": 0.01},
                {"model": "claude", "total": 200, "cost": 0.02},
            ],
        },
    )

    decoded = decode_state(body=body)

    assert_that(decoded.version).is_equal_to(STATE_VERSION)
    assert_that([run.round for run in decoded.runs]).is_equal_to([1, 2])
    assert_that(decoded.runs[1].total).is_equal_to(200)
    assert_that(decoded.findings).is_empty()
    assert_that(decoded.next_round).is_equal_to(3)


def test_migrated_v1_state_is_rewritten_as_v2() -> None:
    """Re-encoding a migrated state stamps the current schema version."""
    decoded = decode_state(body=_wrap({"version": 1, "runs": [{"model": "m"}]}))

    payload = json.loads(encode_state(state=decoded))

    assert_that(payload["version"]).is_equal_to(2)
    assert_that(payload).contains_key("findings")


@pytest.mark.parametrize(
    "body",
    [
        "no state block at all",
        f"{STATE_MARKER_PREFIX} not json at all {STATE_MARKER_SUFFIX}",
        f"{STATE_MARKER_PREFIX} [1, 2, 3] {STATE_MARKER_SUFFIX}",
    ],
    ids=["absent", "malformed-json", "non-mapping"],
)
def test_unreadable_state_yields_empty_state(body: str) -> None:
    """A missing or malformed blob never raises; it starts fresh."""
    decoded = decode_state(body=body)

    assert_that(decoded.runs).is_empty()
    assert_that(decoded.findings).is_empty()
    assert_that(decoded.next_round).is_equal_to(1)


@pytest.mark.parametrize(
    "version",
    [99, "banana", None],
    ids=["future", "non-numeric", "null"],
)
def test_unknown_version_starts_fresh(version: object) -> None:
    """An unknown schema version is discarded rather than guessed at."""
    body = _wrap({"version": version, "runs": [{"model": "claude"}]})

    decoded = decode_state(body=body)

    assert_that(decoded.runs).is_empty()


def test_corrupt_finding_entries_are_dropped_not_fatal() -> None:
    """Findings without a fingerprint are skipped, keeping the rest usable."""
    body = _wrap(
        {
            "version": 2,
            "runs": [],
            "findings": [
                {"severity": "P1"},
                {
                    "fingerprint": "c" * 16,
                    "severity": "nonsense",
                    "status": "weird",
                    "checklist_ids": "not-a-list",
                    "resolved_in": "not-a-mapping",
                },
            ],
        },
    )

    decoded = decode_state(body=body)

    assert_that(decoded.findings).is_length(1)
    assert_that(decoded.findings[0].severity).is_equal_to(Severity.P3)
    assert_that(decoded.findings[0].status).is_equal_to(FindingStatus.OPEN)
    assert_that(decoded.findings[0].checklist_ids).is_empty()
    assert_that(decoded.findings[0].resolved_sha).is_empty()


def test_unrecognized_stored_verdict_does_not_fail_open() -> None:
    """A corrupted verdict never decodes as READY, which would fabricate a pass."""
    body = _wrap(
        {
            "version": 2,
            "runs": [{"model": "claude", "verdict": "totally-bogus"}],
            "findings": [],
        },
    )

    decoded = decode_state(body=body)

    assert_that(decoded.runs[0].verdict).is_equal_to(ReviewVerdict.CHANGES_REQUESTED)


def test_prune_keeps_state_under_the_hard_limit() -> None:
    """Oldest runs are pruned until the whole comment fits GitHub's cap."""
    state = ReviewState(
        runs=tuple(
            RunRecord(round=index, model="claude-sonnet-4-20250514", sha="s" * 40)
            for index in range(1, 200)
        ),
        findings=(_record(fingerprint="d" * 16),),
    )
    body = "x" * (GITHUB_COMMENT_HARD_LIMIT - 4_000)

    pruned = prune_state_to_fit(state=state, body=body)

    total = len(body) + len(render_state_block(state=pruned))
    assert_that(total).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that(pruned.truncated).is_true()
    assert_that(len(pruned.runs)).is_less_than(len(state.runs))
    # Pruning drops the oldest runs first, so the newest survives.
    assert_that(pruned.runs[-1].round).is_equal_to(199)


def test_prune_is_a_no_op_when_state_already_fits() -> None:
    """A state that already fits is returned untouched and unflagged."""
    state = ReviewState(runs=(RunRecord(round=1, model="claude"),))

    pruned = prune_state_to_fit(state=state, body="short body")

    assert_that(pruned).is_equal_to(state)
    assert_that(pruned.truncated).is_false()


def test_prune_drops_resolved_findings_before_open_ones() -> None:
    """When runs cannot be pruned further, resolved findings go first."""
    open_record = _record(fingerprint="e" * 16, since_round=5)
    resolved_records = tuple(
        FindingRecord(
            fingerprint=f"{index:016d}",
            status=FindingStatus.RESOLVED,
            since_round=1,
            resolved_sha="f" * 40,
            resolved_round=2,
            title="Resolved finding with a fairly long title for bulk",
            file="src/some/long/path/module.py",
        )
        for index in range(200)
    )
    state = ReviewState(
        runs=(RunRecord(round=1, model="claude"),),
        findings=(*resolved_records, open_record),
    )
    body = "x" * (GITHUB_COMMENT_HARD_LIMIT - 2_000)

    pruned = prune_state_to_fit(state=state, body=body)

    total = len(body) + len(render_state_block(state=pruned))
    assert_that(total).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that(pruned.truncated).is_true()
    assert_that(pruned.open_findings).is_length(1)
    assert_that(len(pruned.resolved_findings)).is_less_than(len(resolved_records))


def test_prune_gives_up_gracefully_when_body_alone_overflows() -> None:
    """An oversized body yields an empty state rather than an exception."""
    state = ReviewState(
        runs=(RunRecord(round=1, model="claude"),),
        findings=(_record(fingerprint="a" * 16),),
    )

    pruned = prune_state_to_fit(state=state, body="x" * GITHUB_COMMENT_HARD_LIMIT)

    assert_that(pruned.findings).is_empty()
    assert_that(pruned.truncated).is_true()


def test_state_helpers_report_open_and_resolved_partitions() -> None:
    """``open_findings`` and ``resolved_findings`` partition the record set."""
    state = ReviewState(
        findings=(
            _record(fingerprint="1" * 16),
            FindingRecord(fingerprint="2" * 16, status=FindingStatus.RESOLVED),
        ),
    )

    assert_that(state.open_findings).is_length(1)
    assert_that(state.resolved_findings).is_length(1)
