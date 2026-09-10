"""Depth >= 2 pass failures degrade the chunk instead of aborting it (#2395).

The main (depth-1) review call is already paid for by the time the optional
depth-2 question generator or the depth-3 adversarial sweep runs. An
:class:`~lintro.ai.exceptions.AIError` from one of those extra calls used to
propagate out of the chunk, so a sweep timeout discarded the main pass's
findings for that chunk. These tests pin the degrade-and-record behaviour, and
that the two failures which must still stop the run -- a depth-1 failure and a
cost-cap stop -- are unchanged.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.exceptions import AICostBudgetExceededError, AIProviderError
from lintro.ai.providers.response import AIResponse
from lintro.ai.registry import AIProvider
from lintro.ai.review.coverage_degradation import (
    PARTIAL_REVIEW_LABEL,
    describe_coverage_degradations,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.orchestrator import run_review_async
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.sticky import build_sticky_comment

_TIMEOUT_TEXT = "Claude CLI timed out after 600s"


def _chunk_and_context(*, repo_root: str) -> tuple[ReviewChunk, ReviewContext]:
    """Build a one-file chunk and its review context.

    Args:
        repo_root: Absolute path used as the review's repository root.

    Returns:
        The chunk and the context that carries its diff.
    """
    chunk = ReviewChunk(
        id=1,
        files=["src/a.py"],
        diff="+x = 1\n",
        relationship="single-file",
    )
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="src/a.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff=chunk.diff,
        pr_metadata=None,
        repo_root=repo_root,
    )
    return chunk, context


def _response(*, content: str) -> AIResponse:
    """Wrap raw model text in a provider response.

    Args:
        content: The model's answer text.

    Returns:
        A response carrying token usage the chunk can fold in.
    """
    return AIResponse(
        content=content,
        model="claude-sonnet-4-6",
        provider=AIProvider.ANTHROPIC,
        input_tokens=10,
        output_tokens=20,
        cost_estimate=0.01,
    )


def _main_pass_response() -> AIResponse:
    """Return a main-pass answer carrying one finding.

    Returns:
        A parseable chunk review response with a single finding.
    """
    return _response(
        content=json.dumps(
            {
                "summary": {"headline": "Adds a constant.", "walkthrough": []},
                "checklist": [],
                "findings": [
                    {
                        "severity": "major",
                        "file": "src/a.py",
                        "line": 1,
                        "title": "Main-pass finding",
                        "description": "Paid for by the depth-1 call.",
                        "category": "logic-bug",
                    },
                ],
            },
        ),
    )


def _questions_response() -> AIResponse:
    """Return a depth-2 generated-questions answer.

    Returns:
        A parseable generated-questions response.
    """
    return _response(
        content=json.dumps({"generated_questions": [{"question": "Is x used?"}]}),
    )


def _provider() -> MagicMock:
    """Return a provider double the run session can own and close.

    Returns:
        Configured mock provider.
    """
    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    provider.capabilities.supports_sessions = False
    return provider


async def _run(
    *,
    tmp_path: Path,
    depth: int,
    call_ai: AsyncMock,
) -> ReviewResult:
    """Drive a one-chunk review with a scripted provider seam.

    Args:
        tmp_path: Temporary directory used as the repository root.
        depth: Review depth to run at.
        call_ai: The patched ``provider_call.call_ai`` double.

    Returns:
        The finished review result.
    """
    chunk, context = _chunk_and_context(repo_root=str(tmp_path))
    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=[chunk],
        ),
        patch("lintro.ai.review.provider_call.call_ai", new=call_ai),
    ):
        return await run_review_async(
            context=context,
            options=ReviewSessionOptions(
                provider=_provider(),
                ai_config=AIConfig(enabled=True, review=True),
                depth=depth,
                checklist_items=[],
                checklist_text="",
                classifications=[],
            ),
        )


def _adversarial_timeout_seam() -> AsyncMock:
    """Script a depth-3 run whose adversarial sweep times out.

    Returns:
        A ``call_ai`` double answering the depth-2 and main calls and failing
        the third.
    """
    answers = [_questions_response(), _main_pass_response()]

    async def _call(**_kwargs: object) -> AIResponse:
        """Answer the scripted calls, then time out.

        Args:
            **_kwargs: Provider-call keywords the seam ignores.

        Returns:
            The next scripted response.

        Raises:
            AIProviderError: Once the scripted answers run out.
        """
        if answers:
            return answers.pop(0)
        raise AIProviderError(_TIMEOUT_TEXT)

    return AsyncMock(side_effect=_call)


async def test_adversarial_timeout_keeps_the_main_pass_findings(
    tmp_path: Path,
) -> None:
    """A timed-out depth-3 sweep must not discard the depth-1 findings.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _run(
        tmp_path=tmp_path,
        depth=3,
        call_ai=_adversarial_timeout_seam(),
    )

    assert_that([finding.title for finding in result.findings]).is_equal_to(
        ["Main-pass finding"],
    )
    assert_that(result.metadata.partial).is_false()
    assert_that(result.metadata.stopped_reason).is_equal_to("")
    assert_that(result.metadata.chunks_reviewed).is_equal_to(1)


async def test_adversarial_timeout_is_recorded_as_a_degraded_pass(
    tmp_path: Path,
) -> None:
    """The degraded sweep is recorded on the chunk and stays cap-free.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _run(
        tmp_path=tmp_path,
        depth=3,
        call_ai=_adversarial_timeout_seam(),
    )

    degradations = result.metadata.coverage_degradations
    assert_that([item.reason for item in degradations]).is_equal_to(
        [CoverageDegradationReason.ADVERSARIAL_SWEEP_FAILED],
    )
    assert_that(degradations[0].chunk_index).is_equal_to(0)
    assert_that(result.metadata.findings_coverage_complete).is_false()
    # The sweep carries no per-call ceiling, so it must not be read as one.
    assert_that(result.metadata.findings_cap_applied).is_none()


async def test_degraded_sweep_warns_on_every_surface(
    tmp_path: Path,
) -> None:
    """The degraded sweep reaches the shared coverage-limited warning.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _run(
        tmp_path=tmp_path,
        depth=3,
        call_ai=_adversarial_timeout_seam(),
    )

    note = describe_coverage_degradations(metadata=result.metadata)

    assert_that(note).contains("the depth-3 adversarial sweep failed")
    assert_that(note).contains("Every chunk was reviewed")


async def test_both_depth_passes_failing_still_keeps_the_main_pass(
    tmp_path: Path,
) -> None:
    """Losing depth 2 *and* depth 3 costs depth, never the chunk (#2395).

    The two passes are guarded independently, so a chunk that loses both must
    still deliver its main-pass findings and record one degradation per lost
    pass rather than collapsing them into a single "something failed" note.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    calls: list[int] = []

    async def _call(**_kwargs: object) -> AIResponse:
        """Answer only the main pass; fail the depth-2 and depth-3 calls.

        Args:
            **_kwargs: Provider-call keywords the seam ignores.

        Returns:
            The scripted main-pass response.

        Raises:
            AIProviderError: On the first and third calls.
        """
        calls.append(1)
        if len(calls) == 2:
            return _main_pass_response()
        raise AIProviderError(_TIMEOUT_TEXT)

    result = await _run(
        tmp_path=tmp_path,
        depth=3,
        call_ai=AsyncMock(side_effect=_call),
    )

    assert_that([finding.title for finding in result.findings]).is_equal_to(
        ["Main-pass finding"],
    )
    assert_that(result.metadata.partial).is_false()
    assert_that(
        [item.reason for item in result.metadata.coverage_degradations],
    ).is_equal_to(
        [
            CoverageDegradationReason.GENERATED_QUESTIONS_FAILED,
            CoverageDegradationReason.ADVERSARIAL_SWEEP_FAILED,
        ],
    )

    note = describe_coverage_degradations(metadata=result.metadata)

    assert_that(note).contains("the depth-2 generated-questions pass failed")
    assert_that(note).contains("the depth-3 adversarial sweep failed")
    # One chunk lost both passes: the clauses must not double-count it.
    assert_that(note).does_not_contain("2 chunks")


async def test_generated_questions_failure_still_runs_the_main_pass(
    tmp_path: Path,
) -> None:
    """A failed depth-2 pass reviews the chunk against the static checklist.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    calls: list[int] = []

    async def _call(**_kwargs: object) -> AIResponse:
        """Fail the first (depth-2) call, then answer the main pass.

        Args:
            **_kwargs: Provider-call keywords the seam ignores.

        Returns:
            The scripted main-pass response.

        Raises:
            AIProviderError: On the first call.
        """
        calls.append(1)
        if len(calls) == 1:
            raise AIProviderError(_TIMEOUT_TEXT)
        return _main_pass_response()

    result = await _run(
        tmp_path=tmp_path,
        depth=2,
        call_ai=AsyncMock(side_effect=_call),
    )

    assert_that([finding.title for finding in result.findings]).is_equal_to(
        ["Main-pass finding"],
    )
    assert_that(
        [item.reason for item in result.metadata.coverage_degradations],
    ).is_equal_to([CoverageDegradationReason.GENERATED_QUESTIONS_FAILED])


async def test_main_pass_failure_still_aborts_the_chunk(
    tmp_path: Path,
) -> None:
    """A depth-1 failure keeps its pre-#2395 behaviour: the run stops.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _run(
        tmp_path=tmp_path,
        depth=1,
        call_ai=AsyncMock(side_effect=AIProviderError(_TIMEOUT_TEXT)),
    )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).contains("timeout")
    assert_that(result.findings).is_empty()
    assert_that(result.metadata.coverage_degradations).is_empty()


async def test_cost_budget_exceeded_in_the_sweep_still_aborts(
    tmp_path: Path,
) -> None:
    """The cost cap is a graceful stop, never a degraded pass.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    answers = [_questions_response(), _main_pass_response()]

    async def _call(**_kwargs: object) -> AIResponse:
        """Answer the scripted calls, then exhaust the cost budget.

        Args:
            **_kwargs: Provider-call keywords the seam ignores.

        Returns:
            The next scripted response.

        Raises:
            AICostBudgetExceededError: Once the scripted answers run out.
        """
        if answers:
            return answers.pop(0)
        raise AICostBudgetExceededError("AI cost budget of $1.00 reached")

    result = await _run(
        tmp_path=tmp_path,
        depth=3,
        call_ai=AsyncMock(side_effect=_call),
    )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).contains("cost")
    assert_that(
        [
            item.reason
            for item in result.metadata.coverage_degradations
            if item.reason is CoverageDegradationReason.ADVERSARIAL_SWEEP_FAILED
        ],
    ).is_empty()


async def test_a_clean_depth_three_run_records_no_degradation(
    tmp_path: Path,
) -> None:
    """A run whose extra passes all succeed is unchanged by #2395.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    responses = [
        _questions_response(),
        _main_pass_response(),
        _response(content=json.dumps({"findings": []})),
    ]

    async def _call(**_kwargs: object) -> AIResponse:
        """Answer every scripted call in order.

        Args:
            **_kwargs: Provider-call keywords the seam ignores.

        Returns:
            The next scripted response.
        """
        return responses.pop(0)

    result = await _run(
        tmp_path=tmp_path,
        depth=3,
        call_ai=AsyncMock(side_effect=_call),
    )

    assert_that(result.metadata.findings_coverage_complete).is_true()
    assert_that(result.metadata.partial).is_false()


def _classifier() -> ModuleType:
    """Load the CI outcome classifier as an importable module.

    Returns:
        The loaded ``scripts/ci/classify_review_outcome.py`` module.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "scripts"
        / "ci"
        / "classify_review_outcome.py"
    )
    spec = importlib.util.spec_from_file_location("classify_review_outcome", script)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {script}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["classify_review_outcome"] = module
    spec.loader.exec_module(module)
    return module


async def test_a_degraded_run_reddens_the_ai_review_check(
    tmp_path: Path,
) -> None:
    """The recorded envelope of a degraded run classifies as non-success.

    End-to-end on the axis this PR adds: the run degrades, lintro serializes
    ``findings_coverage_complete: false``, and the CI classifier turns that
    into a red check that still names the review as produced.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _run(
        tmp_path=tmp_path,
        depth=3,
        call_ai=_adversarial_timeout_seam(),
    )
    envelope = review_result_to_dict(result=result)
    classifier = _classifier()

    report = classifier.classify(status=0, output=json.dumps(envelope))

    assert_that(envelope["findings_coverage_complete"]).is_false()
    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.DEGRADED)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.detail).contains("adversarial_sweep_failed")
    # The findings were still posted: this is not an "un-reviewed diff".
    assert_that(report.outcome.review_unavailable).is_false()


async def test_a_clean_run_envelope_still_passes_the_check(
    tmp_path: Path,
) -> None:
    """The same pipeline with no degradation keeps the check green.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    responses = [
        _questions_response(),
        _main_pass_response(),
        _response(content=json.dumps({"findings": []})),
    ]

    async def _call(**_kwargs: object) -> AIResponse:
        """Answer every scripted call in order.

        Args:
            **_kwargs: Provider-call keywords the seam ignores.

        Returns:
            The next scripted response.
        """
        return responses.pop(0)

    result = await _run(
        tmp_path=tmp_path,
        depth=3,
        call_ai=AsyncMock(side_effect=_call),
    )
    envelope = review_result_to_dict(result=result)
    classifier = _classifier()

    report = classifier.classify(status=0, output=json.dumps(envelope))

    assert_that(envelope["findings_coverage_complete"]).is_true()
    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.exit_code).is_equal_to(0)


def _degraded_metadata() -> ReviewMetadata:
    """Build metadata for a round whose adversarial sweep failed.

    Returns:
        Metadata carrying one depth-pass coverage degradation.
    """
    return ReviewMetadata(
        model="claude-sonnet-4-6",
        provider="anthropic",
        context_window=200_000,
        depth=3,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
        coverage_degradations=(
            CoverageDegradation(
                reason=CoverageDegradationReason.ADVERSARIAL_SWEEP_FAILED,
                chunk_index=0,
                findings_cap=0,
            ),
        ),
    )


def _rendered_body(*, result: ReviewResult) -> str:
    """Render the per-round GitHub review body through the public builder.

    Args:
        result: Review result to render.

    Returns:
        The rendered Markdown body.
    """
    prior_state = ReviewState()
    return build_review_body(
        result=result,
        prior_state=prior_state,
        match=match_findings(
            previous=prior_state,
            findings=result.findings,
            round_number=prior_state.next_round,
            head_sha="fb740b2",
        ),
        head_sha="fb740b2",
        transport="cli",
        auth_mode="subscription",
    )


def test_the_review_header_announces_a_partial_review(
    sample_review_result: ReviewResult,
) -> None:
    """The header line itself says partial and lists the reason.

    Args:
        sample_review_result: Shared review result fixture.
    """
    result = replace(sample_review_result, metadata=_degraded_metadata())

    header = _rendered_body(result=result).splitlines()[0]

    assert_that(header).contains(PARTIAL_REVIEW_LABEL)
    assert_that(header).does_not_contain("Lintro review")
    assert_that(_rendered_body(result=result)).contains(
        "the depth-3 adversarial sweep failed",
    )


def test_a_complete_review_header_is_unchanged(
    sample_review_result: ReviewResult,
) -> None:
    """An undegraded round keeps its original header line.

    Args:
        sample_review_result: Shared review result fixture.
    """
    header = _rendered_body(result=sample_review_result).splitlines()[0]

    assert_that(header).contains("Lintro review")
    assert_that(header).does_not_contain(PARTIAL_REVIEW_LABEL)


def test_the_sticky_header_announces_a_partial_review(
    sample_review_result: ReviewResult,
) -> None:
    """The sticky board's title line says partial too.

    Args:
        sample_review_result: Shared review result fixture.
    """
    result = replace(sample_review_result, metadata=_degraded_metadata())

    sticky = build_sticky_comment(
        request=StickyRequest(
            result=result,
            transport="cli",
            auth_mode="subscription",
        ),
    )
    title = next(
        line for line in sticky.splitlines() if line.startswith("## 🔎 Lintro Review")
    )

    assert_that(title).contains(PARTIAL_REVIEW_LABEL)
    assert_that(sticky).contains("the depth-3 adversarial sweep failed")
