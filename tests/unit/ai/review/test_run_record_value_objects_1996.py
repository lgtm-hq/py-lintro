"""RunRecord's nested value objects keep the flat sticky-state wire format.

#1996 split the ~30-field flat ``RunRecord`` into four frozen value objects.
The grouping is an in-process shape only: the state blob a review leaves on a
pull request is still flat, with the same key names, the same key order and
the same omit-when-unset rules. These tests hold that line from both sides —
what a record writes, and what it accepts back — so a lintro that predates the
split and one that follows it can read each other's comments.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_outcome import RunOutcome
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.run_record_factory import (
    RoundTotals,
    run_record_from_result,
)

#: The flat keys a record always writes, in the order it writes them. Frozen
#: here on purpose: the sticky state blob is read by every earlier lintro that
#: ever posted on the pull request, so a reordering or a rename is a wire
#: change, not a refactor.
_ALWAYS_WRITTEN = (
    "round",
    "timestamp",
    "sha",
    "model",
    "provider",
    "transport",
    "auth_mode",
    "depth",
    "strictness",
    "files_reviewed",
    "files_skipped",
    "checks",
    "duration",
    "prompt",
    "completion",
    "total",
    "cost",
    "estimated",
    "verdict",
    "confidence",
    "p1",
    "p2",
    "p3",
    "questions",
    "downgraded",
    "partial",
    "chunks_reviewed",
    "chunks_total",
)

#: Keys written only when the value is set, so a record from before they
#: existed re-encodes with no new keys.
_OPTIONAL_KEYS = (
    "coverage_limited",
    "cost_basis",
    "resolved",
    "open_after",
    "narrative",
    "convergence_score",
)


def _full_record() -> RunRecord:
    """Build a record with every group fully populated.

    Returns:
        A record whose every field carries a distinguishable value.
    """
    return RunRecord(
        identity=RunIdentity(
            round=4,
            timestamp="2026-06-24T10:00:00+00:00",
            sha="abc1234",
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            transport="cli",
            auth_mode="subscription",
            depth=2,
            strictness="balanced",
        ),
        coverage=RunCoverage(
            files_reviewed=3,
            files_skipped=1,
            checks=7,
            partial=True,
            coverage_limited=True,
            chunks_reviewed=2,
            chunks_total=3,
        ),
        usage=RunUsage(
            duration=12.34,
            prompt=1000,
            completion=200,
            total=1200,
            cost=0.05,
            estimated=True,
            cost_basis="estimated",
        ),
        outcome=RunOutcome(
            verdict=ReviewVerdict.CHANGES_REQUESTED,
            confidence="high",
            p1=1,
            p2=2,
            p3=3,
            questions=4,
            downgraded=5,
            resolved=6,
            open_after=7,
            narrative="Fixed the fail-open default.",
            convergence_score=1.25,
        ),
    )


def test_a_default_record_writes_exactly_the_always_written_keys() -> None:
    """An unset record emits the flat key list and nothing optional."""
    payload = RunRecord().to_dict()

    assert_that(tuple(payload)).is_equal_to(_ALWAYS_WRITTEN)


def test_a_populated_record_writes_the_flat_keys_in_order() -> None:
    """Every optional key follows the always-written block, in order."""
    payload = _full_record().to_dict()

    assert_that(tuple(payload)).is_equal_to(_ALWAYS_WRITTEN + _OPTIONAL_KEYS)


def test_the_payload_carries_no_group_names() -> None:
    """The nesting never leaks into the blob as ``identity``/``usage``/….

    A nested payload would be unreadable to every lintro that posted on the
    pull request before the split, which is the whole reason the wire format
    stayed flat.
    """
    payload = _full_record().to_dict()

    for group in ("identity", "coverage", "usage", "outcome"):
        assert_that(payload).does_not_contain_key(group)


def test_a_full_record_round_trips_through_the_flat_payload() -> None:
    """Flattening and re-parsing returns an equal record, group by group."""
    record = _full_record()

    restored = RunRecord.from_dict(record.to_dict())

    assert_that(restored).is_equal_to(record)
    assert_that(restored.to_dict()).is_equal_to(record.to_dict())


def test_a_legacy_flat_blob_fills_the_groups_it_can() -> None:
    """A v1-shaped payload parses into the groups with defaults elsewhere.

    The legacy record carries no cost basis, so it is derived from the
    ``auth_mode`` + ``estimated`` pair rather than left blank, and it carries
    none of the per-round counts, which stay ``None`` rather than becoming a
    fabricated zero.
    """
    restored = RunRecord.from_dict(
        {
            "round": 2,
            "sha": "deadbee",
            "model": "claude",
            "auth_mode": "subscription",
            "p1": 3,
        },
    )

    assert_that(restored.identity.round).is_equal_to(2)
    assert_that(restored.identity.sha).is_equal_to("deadbee")
    assert_that(restored.identity.model).is_equal_to("claude")
    assert_that(restored.outcome.p1).is_equal_to(3)
    assert_that(restored.outcome.resolved).is_none()
    assert_that(restored.outcome.open_after).is_none()
    assert_that(restored.outcome.convergence_score).is_none()
    assert_that(restored.usage.cost_basis).is_equal_to("unpriceable")
    assert_that(restored.coverage.coverage_limited).is_false()


def test_the_factory_fills_every_group_from_a_review_result(
    sample_review_result: ReviewResult,
) -> None:
    """``run_record_from_result`` maps a result onto the four groups.

    Args:
        sample_review_result: Representative review result fixture.
    """
    record = run_record_from_result(
        request=StickyRequest(
            result=sample_review_result,
            head_sha="abc1234",
            transport="api",
            auth_mode="api_key",
        ),
        totals=RoundTotals(
            round_number=3,
            verdict=ReviewVerdict.BLOCKED,
            resolved=2,
            open_after=1,
            convergence_score=4.5,
        ),
    )

    metadata = sample_review_result.metadata
    assert_that(record.identity).is_equal_to(
        RunIdentity(
            round=3,
            timestamp=metadata.timestamp,
            sha="abc1234",
            model=metadata.model,
            provider=metadata.provider,
            transport="api",
            auth_mode="api_key",
            depth=metadata.depth,
            strictness=metadata.strictness,
        ),
    )
    assert_that(record.coverage).is_equal_to(
        RunCoverage(
            files_reviewed=metadata.files_reviewed,
            files_skipped=0,
            checks=metadata.checklist_items,
            partial=False,
            coverage_limited=False,
            chunks_reviewed=metadata.chunks_reviewed,
            chunks_total=metadata.chunks_total,
        ),
    )
    assert_that(record.usage).is_equal_to(
        RunUsage(
            duration=metadata.duration_seconds,
            prompt=1000,
            completion=200,
            total=1200,
            cost=metadata.cost_estimate_usd,
            estimated=False,
            cost_basis="billed",
        ),
    )
    assert_that(record.outcome).is_equal_to(
        RunOutcome(
            verdict=ReviewVerdict.BLOCKED,
            p1=1,
            p2=1,
            p3=0,
            questions=0,
            downgraded=0,
            resolved=2,
            open_after=1,
            narrative="Merge with fixes.",
            convergence_score=4.5,
        ),
    )


@pytest.mark.parametrize(
    ("files_total", "files_reviewed", "expected_skipped"),
    [
        pytest.param(7, 3, 4, id="some_files_were_skipped"),
        pytest.param(3, 5, 0, id="a_nonsensical_total_clamps_to_zero"),
    ],
)
def test_the_factory_derives_the_skipped_file_count(
    sample_review_result: ReviewResult,
    files_total: int,
    files_reviewed: int,
    expected_skipped: int,
) -> None:
    """Skipped files are the clamped difference, never a negative count.

    The shared fixture reviews every changed file, so it cannot tell the
    subtraction apart from its inverse or from the group default. Driving the
    factory with metadata that actually skips files pins the direction, and a
    reviewed count above the total pins the clamp: a state blob must never
    carry a negative file count.

    Args:
        sample_review_result: Representative review result fixture.
        files_total: Changed files the round was handed.
        files_reviewed: Changed files the round actually looked at.
        expected_skipped: Skip count the coverage group should record.
    """
    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            files_total=files_total,
            files_reviewed=files_reviewed,
            chunks_reviewed=1,
            duration_seconds=12.5,
        ),
    )

    record = run_record_from_result(
        request=StickyRequest(
            result=result,
            head_sha="abc1234",
            transport="api",
            auth_mode="api_key",
        ),
        totals=RoundTotals(
            round_number=1,
            verdict=ReviewVerdict.BLOCKED,
            resolved=0,
            open_after=2,
            convergence_score=1.5,
        ),
    )

    assert_that(record.coverage.files_skipped).is_equal_to(expected_skipped)
    assert_that(record.coverage.files_reviewed).is_equal_to(files_reviewed)
    assert_that(record.coverage.chunks_reviewed).is_equal_to(1)
    assert_that(record.usage.duration).is_equal_to(12.5)
