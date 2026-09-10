"""The sticky board honours one size contract on every path (issue #2418).

Two behaviours moved verbatim by #2411 broke that promise. The archive branch
re-rendered the primary at default limits and tail-capped the string, so rows
fell off the bottom with nothing saying they had; and the Findings heading
counted the fixed rows *after* ``limits.resolved`` pruning, so a pruned board
reported the round as having fixed fewer things than it did, again with no
marker. Both are size-driven, so both are exercised against a deliberately
over-budget board.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.github_constants import MAX_COMMENT_CHARS, PRIMARY_SOFT_LIMIT
from lintro.ai.review.github_contract import TRUNCATION_NOTICE, RenderLimits
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_outcome import RunOutcome
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.models.sticky_plan import StickyPlan
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky import build_sticky_bodies
from lintro.ai.review.sticky.findings import _findings_round_section

#: Open findings on the over-budget board. Enough that the un-pruned render
#: blows past the hard comment cap, so the archive branch has to prune.
_OVERSIZED_FINDING_COUNT: int = 200

#: Prior rounds on the over-budget board. More than one, which is what sends
#: history to the archive comment.
_PRIOR_ROUND_COUNT: int = 4


def _oversized_result() -> ReviewResult:
    """Build a review result whose findings cannot fit one comment.

    Returns:
        ReviewResult: A result carrying :data:`_OVERSIZED_FINDING_COUNT`
        long-titled findings.
    """
    return ReviewResult(
        metadata=ReviewMetadata(
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            context_window=200_000,
            depth=2,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=3,
            files_total=3,
            checklist_items=3,
            token_usage={"prompt": 1000, "completion": 200, "total": 1200},
            cost_estimate_usd=0.05,
            base_ref="main",
            head_ref="feature",
            timestamp="2026-06-24T10:00:00+00:00",
        ),
        summary="Merge with fixes.",
        findings=tuple(
            ReviewFinding(
                severity=Severity.P2,
                category="logic-bug",
                file=f"src/module_{index:03d}.py",
                line=index + 1,
                title=f"Finding {index:03d} " + "t" * 120,
                description="d" * 200,
                cause="c" * 100,
                fix="f" * 100,
                confidence="high",
            )
            for index in range(_OVERSIZED_FINDING_COUNT)
        ),
    )


def _prior_runs() -> tuple[RunRecord, ...]:
    """Build prior run records with narratives long enough to matter.

    Returns:
        tuple[RunRecord, ...]: :data:`_PRIOR_ROUND_COUNT` records, oldest
        first.
    """
    return tuple(
        RunRecord(
            identity=RunIdentity(round=index, sha=f"{index:07x}", model="m"),
            coverage=RunCoverage(files_reviewed=20, checks=10),
            usage=RunUsage(
                cost=1.0,
                total=10_000,
                prompt=8_000,
                completion=2_000,
                duration=100,
            ),
            outcome=RunOutcome(
                narrative="x" * 400,
                verdict=ReviewVerdict.CHANGES_REQUESTED,
                resolved=2,
                open_after=3,
            ),
        )
        for index in range(1, _PRIOR_ROUND_COUNT + 1)
    )


def _resolved_records(*, count: int, round_number: int) -> tuple[FindingRecord, ...]:
    """Build records resolved in one round.

    Args:
        count: How many records to build.
        round_number: Round the records were resolved in.

    Returns:
        tuple[FindingRecord, ...]: The resolved records.
    """
    return tuple(
        FindingRecord(
            fingerprint=f"fingerprint-{index}",
            severity=Severity.P2,
            category="logic-bug",
            title=f"Fixed thing {index}",
            file=f"src/fixed_{index}.py",
            line=index + 1,
            status=FindingStatus.RESOLVED,
            since_round=round_number - 1,
            resolved_round=round_number,
            resolved_sha="abc1234",
        )
        for index in range(count)
    )


def test_archived_primary_prunes_sections_instead_of_tail_capping() -> None:
    """The archive branch fits the body by pruning, never by tail-capping."""
    primary, archive = build_sticky_bodies(
        request=StickyRequest(
            result=_oversized_result(),
            prior_state=ReviewState(runs=_prior_runs()),
            head_sha="fffffff",
        ),
    )

    assert_that(archive).is_not_none()
    assert_that(len(primary)).is_greater_than(PRIMARY_SOFT_LIMIT)
    assert_that(len(primary)).is_less_than_or_equal_to(MAX_COMMENT_CHARS)
    assert_that(primary).does_not_contain(TRUNCATION_NOTICE.strip())
    assert_that(primary).contains("more open")
    assert_that(primary).contains("not listed**")


def test_findings_heading_counts_fixed_rows_before_pruning() -> None:
    """A pruned fixed list keeps the round's true count and says what went."""
    records = _resolved_records(count=5, round_number=2)
    plan = StickyPlan(
        match=FindingMatchResult(records=records),
        verdict=ReviewVerdict.READY,
        round_number=2,
    )

    section = _findings_round_section(plan=plan, limits=RenderLimits(resolved=2))

    assert_that(section).contains("5 fixed this round")
    assert_that(section).contains("3 more fixed findings not listed**")
    assert_that(section.count("~~Fixed thing")).is_equal_to(2)


def test_findings_heading_is_unmarked_when_nothing_is_pruned() -> None:
    """An unpruned board keeps its plain heading and carries no marker."""
    records = _resolved_records(count=3, round_number=2)
    plan = StickyPlan(
        match=FindingMatchResult(records=records),
        verdict=ReviewVerdict.READY,
        round_number=2,
    )

    section = _findings_round_section(plan=plan, limits=RenderLimits())

    assert_that(section).contains("3 fixed this round")
    assert_that(section).does_not_contain("not listed**")
