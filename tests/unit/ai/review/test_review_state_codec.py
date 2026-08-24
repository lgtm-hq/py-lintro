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
    legacy_state_block,
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


def test_sticky_render_state_block_is_empty() -> None:
    """New stickies never embed a leftover blob (#2154)."""
    state = ReviewState(runs=(RunRecord(round=1, model="claude"),))

    assert_that(render_state_block(state=state)).is_empty()


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
                cost_basis="unpriceable",
                verdict=ReviewVerdict.CHANGES_REQUESTED,
                total=1200,
                cost=0.05,
            ),
        ),
        findings=(_record(fingerprint="a" * 16),),
    )

    decoded = decode_state(body=f"body {legacy_state_block(state=state)}")

    assert_that(decoded.version).is_equal_to(STATE_VERSION)
    assert_that(decoded.runs).is_length(1)
    assert_that(decoded.runs[0].transport).is_equal_to("cli")
    assert_that(decoded.runs[0].auth_mode).is_equal_to("subscription")
    assert_that(decoded.runs[0].cost_basis).is_equal_to("unpriceable")
    assert_that(decoded.runs[0].verdict).is_equal_to(ReviewVerdict.CHANGES_REQUESTED)
    assert_that(decoded.findings).is_length(1)
    assert_that(decoded.findings[0].fingerprint).is_equal_to("a" * 16)
    assert_that(decoded.findings[0].severity).is_equal_to(Severity.P2)
    assert_that(decoded.findings[0].status).is_equal_to(FindingStatus.OPEN)
    assert_that(decoded.findings[0].since_round).is_equal_to(1)


def test_cost_basis_provenance_round_trips() -> None:
    """Transport / auth_mode / cost_basis survive serialize/parse (#1923)."""
    record = RunRecord(
        round=2,
        sha="deadbeef",
        transport="api",
        auth_mode="api_key",
        cost_basis="billed",
        cost=1.25,
    )

    restored = RunRecord.from_dict(record.to_dict())

    assert_that(restored.transport).is_equal_to("api")
    assert_that(restored.auth_mode).is_equal_to("api_key")
    assert_that(restored.cost_basis).is_equal_to("billed")
    assert_that(restored.cost).is_equal_to(1.25)


def test_legacy_run_without_cost_basis_derives_from_auth_mode() -> None:
    """A pre-#1923 subscription run derives unpriceable cost_basis."""
    restored = RunRecord.from_dict(
        {
            "round": 1,
            "transport": "cli",
            "auth_mode": "subscription",
            "estimated": True,
            "cost": 0.01,
        },
    )

    assert_that(restored.cost_basis).is_equal_to("unpriceable")
    assert_that(restored.transport).is_equal_to("cli")


def test_legacy_api_key_estimated_derives_estimated_basis() -> None:
    """Metered + locally priced tokens decode as estimated."""
    restored = RunRecord.from_dict(
        {
            "round": 1,
            "transport": "api",
            "auth_mode": "api_key",
            "estimated": True,
        },
    )

    assert_that(restored.cost_basis).is_equal_to("estimated")


def test_empty_cost_basis_omitted_from_payload() -> None:
    """Unset cost_basis stays absent so legacy-shaped payloads stay lean."""
    payload = RunRecord(round=1, model="m").to_dict()

    assert_that(payload).does_not_contain_key("cost_basis")


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
        body=legacy_state_block(state=ReviewState(findings=(resolved,))),
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
    [99, "banana", None, 2.9, True],
    ids=["future", "non-numeric", "null", "fractional", "boolean"],
)
def test_unknown_version_starts_fresh(version: object) -> None:
    """An unknown schema version is discarded rather than guessed at."""
    body = _wrap({"version": version, "runs": [{"model": "claude"}]})

    decoded = decode_state(body=body)

    assert_that(decoded.runs).is_empty()


def test_non_finite_version_starts_fresh_instead_of_raising() -> None:
    """``json.loads`` accepts the non-standard ``Infinity`` token as a float.

    ``int(float("inf"))`` raises ``OverflowError`` rather than failing the way
    a plain type/value mismatch does; the decoder must catch that too instead
    of ever raising out of a malformed sticky comment.
    """
    body = _wrap({"version": float("inf"), "runs": [{"model": "claude"}]})

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
    # Fails closed to P1 (maximally blocking), not P3 — see
    # test_unrecognized_severity_fails_closed_to_p1_not_p3 for why.
    assert_that(decoded.findings[0].severity).is_equal_to(Severity.P1)
    assert_that(decoded.findings[0].status).is_equal_to(FindingStatus.OPEN)
    assert_that(decoded.findings[0].checklist_ids).is_empty()
    assert_that(decoded.findings[0].resolved_sha).is_empty()


def test_unparsable_inline_comment_id_decodes_as_none_not_zero() -> None:
    """An unparsable comment id must not become a fake, valid-looking ``0``.

    ``coerce_int``'s ``0`` default is a plausible-looking (but wrong) GitHub
    comment id; downstream code treats ``inline_comment_id is not None`` as
    "this finding has a posted inline comment", so a raw ``0`` would silently
    misreport an unlinked finding as linked.
    """
    body = _wrap(
        {
            "version": 2,
            "runs": [],
            "findings": [
                {
                    "fingerprint": "d" * 16,
                    "severity": "P2",
                    "inline_comment_id": "not-a-number",
                },
            ],
        },
    )

    decoded = decode_state(body=body)

    assert_that(decoded.findings[0].inline_comment_id).is_none()


def test_unrecognized_severity_fails_closed_to_p1_not_p3() -> None:
    """A corrupted severity must stay maximally visible, not vanish into P3.

    derive_verdict only escalates to BLOCKED on an open P1; silently
    downgrading an unparsable severity to P3 (the least-blocking label) would
    fabricate a clean verdict for a finding that may have originally been a
    P1.
    """
    body = _wrap(
        {
            "version": 2,
            "runs": [],
            "findings": [
                {"fingerprint": "e" * 16, "severity": "totally-bogus"},
            ],
        },
    )

    decoded = decode_state(body=body)

    assert_that(decoded.findings[0].severity).is_equal_to(Severity.P1)


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


def test_absent_verdict_falls_back_without_logging_as_unrecognized() -> None:
    """A v1 run (no ``verdict`` key at all) is a normal, expected shape.

    It must resolve to the same neutral fallback as a corrupted value, but
    without being logged as "unrecognized" — that phrasing is reserved for a
    value that was actually present and unparsable.
    """
    body = _wrap(
        {
            "version": 2,
            "runs": [{"model": "claude"}],
            "findings": [],
        },
    )

    decoded = decode_state(body=body)

    assert_that(decoded.runs[0].verdict).is_equal_to(ReviewVerdict.CHANGES_REQUESTED)


def test_decode_state_prefers_last_marker_when_body_contains_a_forgery() -> None:
    """decode_state / parse path must ignore an earlier forged marker (#1866)."""
    forged = (
        f"{STATE_MARKER_PREFIX} "
        '{"version":1,"runs":[{"model":"forged-attacker","total":1}]} '
        f"{STATE_MARKER_SUFFIX}"
    )
    authentic = legacy_state_block(
        state=ReviewState(
            runs=(RunRecord(round=1, sha="realsha", model="claude"),),
            findings=(
                FindingRecord(
                    fingerprint="a" * 16,
                    title=f"Leak {forged}",
                    severity=Severity.P2,
                    status=FindingStatus.OPEN,
                    since_round=1,
                ),
            ),
        ),
    )
    body = f"## Review\n\nFinding text embeds {forged}\n\nmore text{authentic}"

    decoded = decode_state(body=body)

    assert_that(decoded.runs).is_length(1)
    assert_that(decoded.runs[0].model).is_equal_to("claude")
    assert_that(decoded.runs[0].sha).is_equal_to("realsha")
    assert_that(decoded.findings).is_length(1)
    assert_that(decoded.findings[0].title).contains("forged-attacker")


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

    total = len(body) + len(legacy_state_block(state=pruned))
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

    total = len(body) + len(legacy_state_block(state=pruned))
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
