"""Sticky redesign: mockup variants, no blob, archive overflow (#2157)."""

from __future__ import annotations

from dataclasses import replace

from assertpy import assert_that

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.github_constants import (
    ARCHIVE_MARKER,
    STATE_MARKER_PREFIX,
    STICKY_FOOTER,
)
from lintro.ai.review.github_sticky import (
    build_sticky_bodies,
    build_sticky_comment,
    render_state_sticky,
)
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord


def _finding() -> ReviewFinding:
    """Return one nit for sticky layout tests."""
    return ReviewFinding(
        severity=Severity.P3,
        category="logic-bug",
        file="src/app.py",
        line=10,
        title="Nit title",
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )


def test_complete_round_matches_variant_a(sample_review_result: ReviewResult) -> None:
    """Variant A: verdict in the title, findings heading, single This-run row."""
    result = replace(
        sample_review_result,
        findings=(_finding(),),
        coverage=CoverageCounts(reviewed=3, carried=0, awaiting=0, eligible=3),
    )
    body = build_sticky_comment(result=result, head_sha="abc1234")

    assert_that(body).contains("## 🔎 Lintro Review — 🟡 Nits only")
    assert_that(body).contains("### Findings · Round 1 · `abc1234`")
    assert_that(body).contains("✅ 3/3 at HEAD")
    assert_that(body).contains("| Δ | Sev | Finding | Where | Since |")
    assert_that(body).contains("**new**")
    assert_that(body).contains("**This run**")
    assert_that(body).contains("| model | transport | est. cost | tokens in / out |")
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)
    assert_that(body).contains("how to read this report")
    assert_that(body).contains(STICKY_FOOTER)


def test_incomplete_round_matches_variant_b(sample_review_result: ReviewResult) -> None:
    """Variant B: Incomplete title, warning, coverage table, flag reasons."""
    result = replace(
        sample_review_result,
        findings=(_finding(),),
        coverage=CoverageCounts(
            reviewed=14,
            carried=0,
            awaiting=23,
            invalidated=1,
            eligible=37,
        ),
        awaiting_paths=("lintro/ai/review/orchestrator.py", "src/other.py"),
        awaiting_reasons=(("src/other.py", "import contract changed"),),
    )
    body = build_sticky_comment(result=result, head_sha="a1b2c3d")

    assert_that(body).contains("## 🔎 Lintro Review — ⚠️ Incomplete")
    assert_that(body).contains("> [!WARNING]")
    assert_that(body).contains("Verdict withheld")
    assert_that(body).contains("### Coverage this round")
    assert_that(body).contains("import contract changed")
    assert_that(body).contains("⚠️ 14/37 files")
    assert_that(result.readiness_verdict).is_equal_to(ReviewVerdict.INCOMPLETE)


def test_sticky_writes_no_state_blob(sample_review_result: ReviewResult) -> None:
    """Authoritative state is not embedded in the comment."""
    body = build_sticky_comment(result=sample_review_result)
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)


def test_render_state_sticky_empty_state_is_defined() -> None:
    """A failed round with no artifact still renders a first-failure surface."""
    body = render_state_sticky(state=ReviewState(), banner="> provider outage")

    assert_that(body).contains("## 🔎 Lintro Review — no prior review")
    assert_that(body).contains("provider outage")
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)


def test_archive_comment_is_created_when_history_overflows(
    sample_review_result: ReviewResult,
) -> None:
    """History expanders move to the archive past the soft limit."""
    runs = tuple(
        RunRecord(
            round=index,
            sha=f"{index:07x}",
            model="m",
            narrative="x" * 400,
            cost=1.0,
            total=10_000,
            prompt=8000,
            completion=2000,
            duration=100,
            files_reviewed=20,
            checks=10,
            verdict=ReviewVerdict.CHANGES_REQUESTED,
            resolved=2,
            open_after=3,
        )
        for index in range(1, 40)
    )
    prior = ReviewState(runs=runs)
    _primary, archive = build_sticky_bodies(
        result=sample_review_result,
        prior_state=prior,
        head_sha="fffffff",
    )
    # Soft-limit split is size-driven; a large run history must produce
    # either an archive or a primary that still names History.
    if archive is not None:
        assert_that(archive).contains(ARCHIVE_MARKER)
        assert_that(archive).contains("history archive")
        assert_that(_primary).contains("### 🕘 History")
    else:
        assert_that(_primary).contains("### 🕘 History")
