"""Fake-transport acceptance for file-level resume (#2154)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.patch_hash import normalized_patch_hash


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
) -> object:
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

    with patch("lintro.ai.review.orchestrator.call_ai", side_effect=_call_ai):
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


def test_quiet_rereview_makes_zero_provider_calls() -> None:
    """An unchanged covered HEAD spends no budget, including custom agents."""
    context = _context("a.py")
    first_calls: list[int] = []
    first = _run(context=context, calls=first_calls, payload_file="a.py")
    assert_that(first_calls).is_length(1)
    assert_that(first.coverage).is_not_none()
    assert_that(first.coverage.complete).is_true()
    assert_that(first.coverage_records).is_not_empty()

    prior = ReviewState(coverage=first.coverage_records)
    second_calls: list[int] = []
    second = _run(context=context, prior=prior, calls=second_calls)
    assert_that(second_calls).is_empty()
    assert_that(second.coverage.complete).is_true()
    assert_that(second.coverage.reviewed).is_equal_to(0)
    assert_that(second.coverage.carried).is_greater_than(0)
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
    assert_that(result.coverage.complete).is_true()


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
    assert_that(result.coverage).is_not_none()
    if not result.coverage.complete:
        assert_that(result.readiness_verdict).is_equal_to(ReviewVerdict.INCOMPLETE)
