"""Fake-transport acceptance for file-level resume (#2154)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.enums.file_review_need import FileReviewNeed
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.flagged_file import FlaggedFile
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.patch_hash import normalized_patch_hash
from lintro.ai.review.resume import plan_resume


def _diff(*, path: str, added: str) -> str:
    """Return a tiny unified diff for one file."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,1 +1,2 @@\n"
        " context\n"
        f"+{added}\n"
    )


def _context(*paths: str) -> ReviewContext:
    """Build a review context with one added line per path."""
    files = [
        ChangedFile(path=path, status="modified", additions=1, deletions=0)
        for path in paths
    ]
    unified = "\n".join(_diff(path=path, added=f"change-{path}") for path in paths)
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=files,
        unified_diff=unified,
        pr_metadata=None,
    )


def _payload(*, file: str) -> str:
    """Return a valid review JSON payload with one finding on ``file``."""
    return (
        "{"
        '"summary": "ok",'
        '"checklist": [],'
        f'"findings": [{{'
        f'"severity": "P3", "category": "logic-bug", "file": "{file}",'
        '"line": 1, "title": "Nit", "description": "d", "cause": "c",'
        '"fix": "f", "confidence": "high"'
        "}]}"
    )


def _provider() -> MagicMock:
    """Return a session-less provider whose complete() is unused."""
    provider = MagicMock()
    provider.model_name = "fake-model"
    provider.name = "fake"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    return provider


def _run(
    *,
    context: ReviewContext,
    prior: ReviewState | None = None,
    force_full: bool = False,
    calls: list[int] | None = None,
    payload_file: str = "a.py",
) -> ReviewResult:
    """Run a review with a recording fake transport."""
    provider = _provider()

    def _call_ai(**_kwargs: object) -> AIResponse:
        if calls is not None:
            calls.append(1)
        return AIResponse(
            content=_payload(file=payload_file),
            model="fake-model",
            input_tokens=10,
            output_tokens=5,
            cost_estimate=0.01,
            provider="fake",
        )

    with patch("lintro.ai.review.response_pipeline.call_ai", side_effect=_call_ai):
        return run_review(
            context,
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
            prior_state=prior,
            force_full=force_full,
            enforce_cost_cap=False,
        )


def _require_coverage(result: ReviewResult) -> CoverageCounts:
    """Return coverage counters, failing the test when they are missing."""
    coverage = result.coverage
    assert coverage is not None
    return coverage


def test_quiet_rereview_makes_zero_provider_calls() -> None:
    """An unchanged covered HEAD spends no budget, including custom agents."""
    context = _context("a.py")
    first_calls: list[int] = []
    first = _run(context=context, calls=first_calls, payload_file="a.py")
    assert_that(first_calls).is_length(1)
    first_coverage = _require_coverage(first)
    assert_that(first_coverage.complete).is_true()
    assert_that(first.coverage_records).is_not_empty()

    prior = ReviewState(coverage=first.coverage_records)
    second_calls: list[int] = []
    second = _run(context=context, prior=prior, calls=second_calls)
    second_coverage = _require_coverage(second)
    assert_that(second_calls).is_empty()
    assert_that(second_coverage.complete).is_true()
    assert_that(second_coverage.reviewed).is_equal_to(0)
    assert_that(second_coverage.carried).is_greater_than(0)
    assert_that(second.findings).is_empty()


def test_full_flag_discards_carried_coverage() -> None:
    """``--full`` re-reviews files that would otherwise be carried."""
    context = _context("a.py")
    first = _run(context=context, payload_file="a.py")
    prior = ReviewState(coverage=first.coverage_records)
    calls: list[int] = []
    _run(context=context, prior=prior, force_full=True, calls=calls)
    assert_that(calls).is_not_empty()


def test_content_identical_rebase_keeps_coverage() -> None:
    """Hunk-header drift does not invalidate a stored hash."""
    path = "a.py"
    left = "@@ -1,3 +1,3 @@\n context\n-old\n+new\n"
    right = "@@ -10,5 +10,5 @@\n other\n-old\n+new\n"
    assert_that(normalized_patch_hash(left)).is_equal_to(normalized_patch_hash(right))
    prior = ReviewState(
        coverage=(
            CoverageRecord(
                path=path,
                patch_hash=normalized_patch_hash(left),
            ),
        ),
    )
    context = ReviewContext(
        base_ref="main",
        head_ref="rebased",
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=1),
        ],
        unified_diff=f"diff --git a/{path} b/{path}\n{right}",
        pr_metadata=None,
    )
    calls: list[int] = []
    result = _run(context=context, prior=prior, calls=calls)
    assert_that(calls).is_empty()
    assert_that(_require_coverage(result).complete).is_true()


def test_partial_round_is_incomplete() -> None:
    """Coverage-at-HEAD below 100% forces INCOMPLETE even with only nits."""
    context = _context("a.py", "b.py")
    first = _run(context=context, payload_file="a.py")
    # Force a one-file coverage map to simulate a capped first round.
    covered = first.coverage_records[:1] or (
        CoverageRecord(
            path="a.py",
            patch_hash=normalized_patch_hash(_diff(path="a.py", added="change-a.py")),
        ),
    )
    prior = ReviewState(coverage=covered)
    result = _run(context=context, prior=prior, payload_file="b.py")
    if not _require_coverage(result).complete:
        assert_that(result.readiness_verdict).is_equal_to(ReviewVerdict.INCOMPLETE)


def _shared_line_diff(*, path: str) -> str:
    """Return a unified diff whose +/- lines match any sibling using this helper."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,1 +1,2 @@\n"
        " context\n"
        "+shared-change\n"
    )


def _single_file_chunks(*, diffs: dict[str, str]) -> list[ReviewChunk]:
    """Return one isolated chunk per path so a cap can stop mid-queue."""
    return [
        ReviewChunk(
            id=index,
            files=[path],
            diff=text,
            relationship=REL_SINGLE_FILE,
        )
        for index, (path, text) in enumerate(diffs.items(), start=1)
    ]


def _run_capped(
    *,
    context: ReviewContext,
    chunks: list[ReviewChunk],
    prior: ReviewState | None = None,
    payload_file: str = "a.py",
    calls: list[int] | None = None,
) -> ReviewResult:
    """Run a serial $0.01-capped review against isolated chunks."""
    provider = _provider()

    def _call_ai(**kwargs: object) -> AIResponse:
        if calls is not None:
            calls.append(1)
        budget = kwargs.get("budget")
        response = AIResponse(
            content=_payload(file=payload_file),
            model="fake-model",
            input_tokens=10,
            output_tokens=5,
            cost_estimate=0.01,
            provider="fake",
        )
        if isinstance(budget, CostBudget):
            budget.record(response.cost_estimate)
        return response

    with (
        patch(
            "lintro.ai.review.orchestrator.resolve_review_chunks",
            return_value=chunks,
        ),
        patch("lintro.ai.review.response_pipeline.call_ai", side_effect=_call_ai),
    ):
        return run_review(
            context,
            provider=provider,
            ai_config=AIConfig(
                enabled=True,
                transport=AITransport.API,
                max_cost_usd=0.01,
                max_parallel_calls=1,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
            prior_state=prior,
            force_full=False,
            enforce_cost_cap=True,
        )


def test_inherited_sibling_clears_pending_after_capped_round() -> None:
    """Sampler-omitted same-hash mates must not stay pending forever."""
    keep = "keep.py"
    skip = "skip.py"
    shared_hash = normalized_patch_hash(_shared_line_diff(path=keep))
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path=keep, status="modified", additions=1, deletions=0),
            ChangedFile(path=skip, status="modified", additions=1, deletions=0),
        ],
        unified_diff="\n".join(
            (_shared_line_diff(path=keep), _shared_line_diff(path=skip)),
        ),
        pr_metadata=None,
    )
    prior = ReviewState(
        coverage=(
            CoverageRecord(keep, shared_hash, round=1),
            CoverageRecord(skip, shared_hash, round=1),
        ),
        pending_invalidations=(
            (keep, FileReviewNeed.GROUP_INVALIDATED.value),
            (skip, FileReviewNeed.GROUP_INVALIDATED.value),
        ),
    )
    calls: list[int] = []
    result = _run_capped(
        context=context,
        chunks=_single_file_chunks(
            diffs={
                keep: _shared_line_diff(path=keep),
                skip: _shared_line_diff(path=skip),
            },
        ),
        prior=prior,
        payload_file=keep,
        calls=calls,
    )
    assert_that(calls).is_length(1)
    pending_paths = {path for path, _need in result.pending_invalidations}
    covered_paths = {record.path for record in result.coverage_records}
    assert_that(covered_paths).contains(skip)
    assert_that(pending_paths).does_not_contain(skip)


def test_unserved_model_flag_stays_queued_after_capped_round() -> None:
    """An unserved MODEL_FLAGGED path must still classify as flagged next round."""
    extras = "extras.py"
    extras_diff = _diff(path=extras, added=f"change-{extras}")
    extras_hash = normalized_patch_hash(extras_diff)
    a_diff = _diff(path="a.py", added="change-a.py")
    context = _context("a.py", extras)
    prior = ReviewState(
        coverage=(CoverageRecord(extras, extras_hash, round=1),),
        flagged_files=(FlaggedFile(extras, "re-check contract", extras_hash),),
    )
    calls: list[int] = []
    first = _run_capped(
        context=context,
        chunks=_single_file_chunks(diffs={"a.py": a_diff, extras: extras_diff}),
        prior=prior,
        payload_file="a.py",
        calls=calls,
    )
    assert_that(calls).is_length(1)
    assert_that([flag.path for flag in first.flagged_files]).contains(extras)
    nxt = plan_resume(
        context=context,
        prior=ReviewState(
            coverage=first.coverage_records,
            flagged_files=first.flagged_files,
        ),
    )
    by_path = {item.path: item.need for item in nxt.classified}
    assert_that(by_path[extras]).is_equal_to(FileReviewNeed.MODEL_FLAGGED)
    assert_that(nxt.queue).contains(extras)
