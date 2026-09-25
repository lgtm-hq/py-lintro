"""Tests for the one-file turn-limit fix (issue #2731).

A one-file clean PR hit the CLI transport's 12-turn limit twice and was left
unreviewed while the run reported like a pass. Three things changed, one
test each: the repository-context section admits every changed text file
(a workflow YAML had none); the retry after a turn limit is single-shot
(no generated questions, no tools) rather than the same call again; a run
whose every chunk reviewed nothing is a stopped run with no narrative and a
non-zero exit.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.cli_bounds import CliCallOptions, current_cli_call_options
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIProviderError, AITurnLimitError
from lintro.ai.invoke import call_ai
from lintro.ai.providers.response import AIResponse
from lintro.ai.review import chunk_split_retry, provider_call, response_pipeline
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.path_utils import is_context_eligible_path, is_source_code_path
from lintro.ai.review.repo_context import RepoContextSource, build_repo_context
from lintro.ai.review.response_pipeline import ChunkReviewRequest
from lintro.ai.review.session import NOTHING_REVIEWED_REASON
from lintro.ai.review.synthesis import should_run_synthesis
from lintro.config.review_config import ReviewSynthesisConfig

_WORKFLOW = ".github/workflows/publish-pypi-on-tag.yml"
_DIFF = (
    f"diff --git a/{_WORKFLOW} b/{_WORKFLOW}\n"
    f"--- a/{_WORKFLOW}\n+++ b/{_WORKFLOW}\n"
    "@@ -236,3 +236,4 @@ jobs:\n       contents: write\n+      actions: read\n"
    "     with:\n       release_tag: x\n"
)
_HEAD = "jobs:\n  homebrew-tap:\n    permissions:\n      contents: write\n      actions: read\n"


# --- (2) the context section admits every changed text file -------------------


@pytest.mark.parametrize(
    ("path", "eligible"),
    [
        (_WORKFLOW, True),
        ("pyproject.toml", True),
        ("docs/ai-features.md", True),
        ("renovate.json", True),
        ("scripts/ci/run", True),
        ("README", True),
        (".env", False),
        ("lintro/ai/review/severity_gate.py", True),
        ("assets/logo.png", False),
        ("assets/logo.svg", False),
        ("tests/__snapshots__/out.yml", False),
        ("data/rows.csv", False),
        ("tests/fixtures/test_data.csv", False),
        ("tests/unit/test_gate.py", True),
    ],
)
def test_context_eligibility_is_every_text_file_but_media_and_data(
    path: str,
    eligible: bool,
) -> None:
    """Workflows, configs and docs qualify; binaries, media and fixtures do not.

    Args:
        path: Changed path.
        eligible: Whether its head content belongs in the prompt.
    """
    assert_that(is_context_eligible_path(path)).is_equal_to(eligible)


def test_the_test_pairing_predicate_is_unchanged() -> None:
    """A workflow still cannot own a test: the wider rule is the context's only."""
    assert_that(is_source_code_path(_WORKFLOW)).is_false()


def test_a_workflow_only_chunk_gets_its_head_content() -> None:
    """The #2518 shape: one YAML file, whose post-change content now renders."""
    reads: list[str] = []

    def _read(path: str) -> str | None:
        reads.append(path)
        return _HEAD if path == _WORKFLOW else None

    section = build_repo_context(
        chunk=ReviewChunk(
            id=1,
            files=[_WORKFLOW],
            diff=_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
        context=ReviewContext(
            base_ref="base",
            head_ref="head",
            changed_files=[
                ChangedFile(
                    path=_WORKFLOW,
                    status="modified",
                    additions=1,
                    deletions=0,
                ),
            ],
            unified_diff=_DIFF,
            pr_metadata=None,
        ),
        source=RepoContextSource(reader=_read),
    )

    assert_that(reads).is_equal_to([_WORKFLOW])
    assert_that(section.files).is_length(1)
    assert_that(section.files[0].path).is_equal_to(_WORKFLOW)
    assert_that(section.files[0].text).is_equal_to(_HEAD)
    assert_that(section.tokens).is_greater_than(0)


# --- (1) the retry is single-shot ----------------------------------------------


def _request(**overrides: Any) -> ChunkReviewRequest:
    """Build a chunk request carrying generated questions.

    Args:
        **overrides: Fields to replace.

    Returns:
        The request.
    """
    fields: dict[str, Any] = {
        "chunk": ReviewChunk(
            id=1,
            files=[_WORKFLOW],
            diff=_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
        "context": ReviewContext(
            base_ref="base",
            head_ref="head",
            changed_files=[
                ChangedFile(
                    path=_WORKFLOW,
                    status="modified",
                    additions=1,
                    deletions=0,
                ),
            ],
            unified_diff=_DIFF,
            pr_metadata=None,
        ),
        "provider": object(),
        "ai_config": AIConfig(enabled=True, transport=AITransport.CLI),
        "checklist_text": "",
        "checklist_count": 0,
        "interaction_paths": "",
        "lint_results": None,
        "extra_checklist": "G1. Does publish-binaries.yml declare actions: read?",
        "strictness_section": "",
        "budget": None,
        "repo_root": "",
        "use_one_shot": True,
        "diff_budget": 100_000,
        "chunk_index": 0,
    }
    fields.update(overrides)
    return ChunkReviewRequest(**fields)


async def test_the_retry_after_a_turn_limit_is_single_shot() -> None:
    """The second attempt carries ``single_shot``; the first did not."""
    seen: list[ChunkReviewRequest] = []

    async def _invoke(*, request: ChunkReviewRequest) -> Any:
        seen.append(request)
        if len(seen) == 1:
            raise AITurnLimitError("a", input_tokens=1, output_tokens=1, turns=13)
        return "call"

    parsed = ChunkReviewPartial(
        findings=(),
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        files=(_WORKFLOW,),
        turns=1,
    )
    with (
        patch.object(chunk_split_retry, "invoke_chunk_review", _invoke),
        patch.object(chunk_split_retry, "_parse_call", AsyncMock(return_value=parsed)),
    ):
        result = await chunk_split_retry.review_chunk_main_pass(request=_request())

    assert_that([r.single_shot for r in seen]).is_equal_to([False, True])
    assert_that(seen[1]).is_equal_to(replace(seen[0], single_shot=True))
    assert_that(result.files).is_equal_to((_WORKFLOW,))


async def test_a_single_shot_call_drops_the_questions_and_the_tools() -> None:
    """``single_shot`` reaches the prompt (no questions) and the call (no tools)."""
    captured: dict[str, Any] = {}

    async def _call_ai(**kwargs: Any) -> AIResponse:
        captured.update(kwargs)
        return AIResponse(content="{}", model="m", provider="anthropic")

    with patch.object(provider_call, "call_ai", _call_ai):
        await response_pipeline.invoke_chunk_review(
            request=_request(single_shot=True),
        )

    assert_that(captured["no_tools"]).is_true()
    assert_that(captured["user_prompt"]).does_not_contain(
        "publish-binaries.yml declare",
    )
    assert_that(captured["user_prompt"]).contains("actions: read")

    captured.clear()
    with patch.object(provider_call, "call_ai", _call_ai):
        await response_pipeline.invoke_chunk_review(request=_request())

    assert_that(captured["no_tools"]).is_false()
    assert_that(captured["user_prompt"]).contains("publish-binaries.yml declare")


async def test_output_exhaustion_on_the_single_shot_retry_splits_single_shot() -> None:
    """A turn limit then output exhaustion: the split halves stay single-shot."""
    seen: list[ChunkReviewRequest] = []
    two_files = replace(
        _request(),
        chunk=ReviewChunk(
            id=1,
            files=[_WORKFLOW, "docs/other.md"],
            diff=_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
    )

    async def _invoke(*, request: ChunkReviewRequest) -> Any:
        seen.append(request)
        if len(seen) == 1:
            raise AITurnLimitError("a", input_tokens=1, output_tokens=1, turns=13)
        if len(seen) == 2:
            raise AIProviderError('stop_reason":"max_tokens')
        return "call"

    parsed = ChunkReviewPartial(
        findings=(),
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        files=(_WORKFLOW,),
        turns=1,
    )
    with (
        patch.object(chunk_split_retry, "invoke_chunk_review", _invoke),
        patch.object(chunk_split_retry, "_parse_call", AsyncMock(return_value=parsed)),
    ):
        result = await chunk_split_retry.review_chunk_main_pass(request=two_files)

    # First attempt, the single-shot retry, then one call per half.
    assert_that([r.single_shot for r in seen]).is_equal_to([False, True, True, True])
    assert_that([len(r.chunk.files) for r in seen]).is_equal_to([2, 2, 1, 1])
    assert_that(result.files).is_not_empty()


async def test_a_split_half_gets_its_own_single_shot_retry() -> None:
    """Output exhaustion then a turn limit on one half: that half retries."""
    seen: list[ChunkReviewRequest] = []
    two_files = replace(
        _request(),
        chunk=ReviewChunk(
            id=1,
            files=[_WORKFLOW, "docs/other.md"],
            diff=_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
    )

    async def _invoke(*, request: ChunkReviewRequest) -> Any:
        seen.append(request)
        if len(seen) == 1:
            raise AIProviderError('stop_reason":"max_tokens')
        if len(seen) == 2:
            raise AITurnLimitError("a", input_tokens=1, output_tokens=1, turns=13)
        return "call"

    parsed = ChunkReviewPartial(
        findings=(),
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        files=(_WORKFLOW,),
        turns=1,
    )
    with (
        patch.object(chunk_split_retry, "invoke_chunk_review", _invoke),
        patch.object(chunk_split_retry, "_parse_call", AsyncMock(return_value=parsed)),
    ):
        await chunk_split_retry.review_chunk_main_pass(request=two_files)

    # Whole chunk, first half (limit), first half single-shot, second half.
    assert_that([r.single_shot for r in seen]).is_equal_to([False, False, True, False])


async def test_the_schema_reminder_after_a_single_shot_retry_stays_single_shot() -> (
    None
):
    """A malformed single-shot answer is re-asked without tools too."""
    captured: list[dict[str, Any]] = []

    async def _call_ai(**kwargs: Any) -> AIResponse:
        captured.append(kwargs)
        return AIResponse(content="{}", model="m", provider="anthropic")

    with patch.object(provider_call, "call_ai", _call_ai):
        await response_pipeline.parse_review_payload_with_recovery(
            response=AIResponse(content="not json at all", model="m", provider="a"),
            request=_request(single_shot=True),
            elapsed=0.1,
        )

    assert_that(captured).is_length(1)
    assert_that(captured[0]["no_tools"]).is_true()


async def test_no_depth_3_sweep_over_a_chunk_the_main_pass_never_reviewed() -> None:
    """Two turn-limited attempts end the chunk; no third, tool-enabled call."""
    from lintro.ai.review import chunk_pass

    calls: list[str] = []
    limited = ChunkReviewPartial(
        findings=(),
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        files=(),
        turns=26,
        coverage_degradations=(
            CoverageDegradation(
                reason=CoverageDegradationReason.TURN_LIMIT_REACHED,
                chunk_index=0,
                limit=12,
            ),
        ),
    )

    async def _main(**_kwargs: Any) -> ChunkReviewPartial:
        calls.append("main")
        return limited

    async def _sweep(**_kwargs: Any) -> Any:
        calls.append("sweep")
        raise AssertionError("the sweep must not run")

    base = _request()
    plan = MagicMock()
    plan.depth = 3
    plan.timings = None
    plan.progress = None
    plan.ai_config = base.ai_config
    plan.classifications = []
    plan.generated_questions = ""
    plan.context = base.context
    plan.provider = MagicMock()
    plan.checklist_text = ""
    plan.checklist_items = []
    plan.lint_results = None
    plan.strictness_section = ""
    plan.budget = CostBudget(max_cost_usd=None)
    plan.repo_root = ""
    plan.use_one_shot = True
    plan.diff_budget = 100_000
    plan.repo_context = None
    plan.context_budget = None
    with (
        patch.object(chunk_pass, "review_chunk_main_pass", _main),
        patch.object(chunk_pass, "run_adversarial_pass", _sweep),
    ):
        partial = await chunk_pass.review_chunk(
            chunk=base.chunk,
            chunk_index=0,
            plan=plan,
        )

    assert_that(calls).is_equal_to(["main"])
    assert_that(partial.files).is_empty()


def _parsed(*files: str) -> ChunkReviewPartial:
    """A parsed partial covering ``files``.

    Args:
        *files: The files it reviewed.

    Returns:
        The partial.
    """
    return ChunkReviewPartial(
        findings=(),
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        files=tuple(files),
        turns=1,
    )


async def _drive(
    request: ChunkReviewRequest,
    script: list[Exception | str],
) -> tuple[ChunkReviewPartial, list[ChunkReviewRequest]]:
    """Run the main pass with the provider answering from ``script``.

    Args:
        request: The chunk request.
        script: Per call: an exception to raise, or ``"ok"`` to answer.

    Returns:
        The partial and every request the provider saw, in order.
    """
    seen: list[ChunkReviewRequest] = []

    async def _invoke(*, request: ChunkReviewRequest) -> Any:
        seen.append(request)
        step = script[len(seen) - 1]
        if isinstance(step, Exception):
            raise step
        return "call"

    async def _parse(*, request: ChunkReviewRequest, call: Any) -> ChunkReviewPartial:
        return _parsed(*request.chunk.files)

    with (
        patch.object(chunk_split_retry, "invoke_chunk_review", _invoke),
        patch.object(chunk_split_retry, "_parse_call", _parse),
    ):
        partial = await chunk_split_retry.review_chunk_main_pass(request=request)
    return partial, seen


def _limit(turns: int = 13) -> AITurnLimitError:
    return AITurnLimitError(
        "limit",
        input_tokens=10,
        output_tokens=1,
        cost_estimate=0.1,
        turns=turns,
    )


def _exhausted() -> AIProviderError:
    return AIProviderError('stop_reason":"max_tokens')


async def test_row_5_single_file_unchanged_retry_then_limit_goes_single_shot() -> None:
    """Exhaustion on one file, then a turn limit on the unchanged retry."""
    partial, seen = await _drive(_request(), [_exhausted(), _limit(), "ok"])

    assert_that([r.single_shot for r in seen]).is_equal_to([False, False, True])
    assert_that(partial.files).is_equal_to((_WORKFLOW,))
    reasons = [d.reason for d in partial.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED)
    assert_that(reasons).does_not_contain(CoverageDegradationReason.TURN_LIMIT_REACHED)


async def test_row_6_single_file_single_shot_exhaustion_then_limit_is_terminal() -> (
    None
):
    """Turn limit → single-shot exhaustion → unchanged retry → turn limit: done.

    The path the two reviewers named: it records ``TURN_LIMIT_REACHED`` and
    returns, rather than raising.
    """
    partial, seen = await _drive(_request(), [_limit(), _exhausted(), _limit()])

    assert_that([r.single_shot for r in seen]).is_equal_to([False, True, True])
    assert_that(partial.files).is_empty()
    reasons = [d.reason for d in partial.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.TURN_LIMIT_REACHED)
    # Both limited attempts are billed once each.
    assert_that(partial.input_tokens).is_equal_to(20)


def _two_file_request() -> ChunkReviewRequest:
    return replace(
        _request(),
        chunk=ReviewChunk(
            id=1,
            files=[_WORKFLOW, "docs/other.md"],
            diff=_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
    )


async def test_a_limited_half_keeps_the_surviving_halves_files() -> None:
    """One half limited twice, the other reviewed: its files (and sweep) stay."""
    from lintro.ai.review import chunk_pass

    partial, seen = await _drive(
        _two_file_request(),
        [_exhausted(), _limit(), _limit(), "ok"],
    )

    assert_that([r.single_shot for r in seen]).is_equal_to([False, False, True, False])
    assert_that(partial.files).is_equal_to(("docs/other.md",))
    reasons = [d.reason for d in partial.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.TURN_LIMIT_REACHED)
    assert_that(partial.input_tokens).is_equal_to(21)

    # chunk_pass treats the partial's files as authoritative once a turn
    # limit is on record, so the surviving half is not erased.
    with patch.object(
        chunk_pass,
        "review_chunk_main_pass",
        AsyncMock(return_value=partial),
    ):
        base = _request()
        plan = MagicMock()
        plan.depth = 1
        plan.timings = None
        plan.progress = None
        plan.ai_config = base.ai_config
        plan.classifications = []
        plan.generated_questions = ""
        plan.context = base.context
        plan.provider = MagicMock()
        plan.checklist_text = ""
        plan.checklist_items = []
        plan.lint_results = None
        plan.strictness_section = ""
        plan.budget = CostBudget(max_cost_usd=None)
        plan.repo_root = ""
        plan.use_one_shot = True
        plan.diff_budget = 100_000
        plan.repo_context = None
        plan.context_budget = None
        scoped = await chunk_pass.review_chunk(
            chunk=_two_file_request().chunk,
            chunk_index=0,
            plan=plan,
        )
    assert_that(scoped.files).is_equal_to(("docs/other.md",))


async def test_a_halves_failed_single_shot_retry_keeps_its_billed_usage() -> None:
    """Half limited, then its single-shot retry fails outright: usage kept."""
    partial, seen = await _drive(
        _two_file_request(),
        [_exhausted(), _limit(), AIProviderError("boom"), "ok"],
    )

    assert_that(partial.files).is_equal_to(("docs/other.md",))
    # The limited first attempt (10) plus the surviving half (1).
    assert_that(partial.input_tokens).is_equal_to(11)
    reasons = [d.reason for d in partial.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.SPLIT_HALF_FAILED)


async def test_row_3_on_a_half_makes_no_further_call() -> None:
    """Whole limit → single-shot exhaustion → halves: a limited half is done."""
    partial, seen = await _drive(
        _two_file_request(),
        [_limit(), _exhausted(), _limit(), "ok"],
    )

    # Whole (tools), whole (single-shot), half 1 (single-shot, limited),
    # half 2 (single-shot): no extra call for the limited half.
    assert_that([r.single_shot for r in seen]).is_equal_to([False, True, True, True])
    assert_that(seen).is_length(4)
    assert_that(partial.files).is_equal_to(("docs/other.md",))
    reasons = [d.reason for d in partial.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.TURN_LIMIT_REACHED)
    # The limited half's row names that half alone, so a rerun redoes it
    # and not the half that answered (#2803).
    (limited,) = [
        d
        for d in partial.coverage_degradations
        if d.reason is CoverageDegradationReason.TURN_LIMIT_REACHED
    ]
    assert_that(limited.paths).is_equal_to(tuple(seen[2].chunk.files))
    assert_that(limited.paths).does_not_contain("docs/other.md")


async def test_row_8_on_a_half_is_lost_not_split_again() -> None:
    """Whole exhaustion → half limit → half single-shot exhaustion: lost half."""
    partial, seen = await _drive(
        _two_file_request(),
        [_exhausted(), _limit(), _exhausted(), "ok"],
    )

    # Whole, half 1 (tools, limited), half 1 (single-shot, exhausted), half
    # 2: no unchanged retry and no second split for half 1.
    assert_that(seen).is_length(4)
    assert_that([len(r.chunk.files) for r in seen]).is_equal_to([2, 1, 1, 1])
    assert_that(partial.files).is_equal_to(("docs/other.md",))
    reasons = [d.reason for d in partial.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.SPLIT_HALF_FAILED)
    # The limited first attempt on the lost half stays billed.
    assert_that(partial.input_tokens).is_equal_to(11)


class _Recorder:
    """A provider double that records the bounds in force when called."""

    model_name = "m"

    def __init__(self) -> None:
        self.seen: list[CliCallOptions | None] = []

    async def complete(self, prompt: str, **kwargs: Any) -> AIResponse:
        self.seen.append(current_cli_call_options())
        return AIResponse(content="ok", model="m", provider="anthropic")


async def test_call_ai_binds_tools_disabled_and_keeps_the_turn_limit() -> None:
    """``no_tools`` reaches the provider as a bound; the 12-turn cap stays."""
    provider = _Recorder()
    await call_ai(
        provider=provider,  # type: ignore[arg-type]
        ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
        user_prompt="p",
        system_prompt=None,
        budget=None,
        no_tools=True,
    )
    assert_that(provider.seen).is_equal_to(
        [CliCallOptions(max_turns=12, tools_disabled=True)],
    )


# --- (3) a run that reviewed nothing says so ----------------------------------


def _partial(*, files: tuple[str, ...]) -> ChunkReviewPartial:
    return ChunkReviewPartial(
        findings=(),
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        files=files,
        turns=1,
    )


async def _finalize(*, partials: list[ChunkReviewPartial]) -> Any:
    """Run the production finalizer over ``partials`` with synthesis stubbed.

    Args:
        partials: The chunk partials the run collected.

    Returns:
        ``(outcome, synthesis_calls)``.
    """
    from lintro.ai.review import run_execution, run_finalize
    from lintro.ai.review.session import ReviewSessionOptions

    calls: list[Any] = []

    async def _synthesis(*, request: Any) -> Any:
        calls.append(request)
        raise AssertionError("synthesis must not run")

    plan = MagicMock()
    plan.ai_config = AIConfig(enabled=True, transport=AITransport.CLI)
    plan.policy = MagicMock()
    plan.timings = MagicMock()
    progress = run_execution.RunProgress()
    options = ReviewSessionOptions(
        provider=MagicMock(),
        ai_config=plan.ai_config,
        depth=1,
        checklist_items=[],
        checklist_text="",
        classifications=[],
        synthesis=ReviewSynthesisConfig(enabled=True),
    )
    merged = MagicMock()
    with (
        patch.object(run_finalize, "run_synthesis_pass", _synthesis),
        patch.object(
            run_finalize,
            "finalize_partials",
            return_value=(merged, (), 0),
        ),
    ):
        outcome = await run_finalize.finalize_completed_run(
            context=_request().context,
            options=options,
            plan=plan,
            progress=progress,
            partials=partials,
            provider_seconds=1.0,
            interrupt=asyncio.Event(),
        )
    return outcome, calls


async def test_a_run_whose_chunks_reviewed_nothing_skips_synthesis() -> None:
    """Through the finalizer: no narrative, stopped, the reason on the outcome."""
    outcome, calls = await _finalize(partials=[_partial(files=()), _partial(files=())])

    assert_that(calls).is_empty()
    assert_that(outcome.partial).is_true()
    assert_that(outcome.stopped_reason).is_equal_to(NOTHING_REVIEWED_REASON)


async def test_a_run_with_no_chunk_at_all_is_not_the_nothing_reviewed_case() -> None:
    """Zero partials (custom agents only, nothing to review) stay a completed run."""
    outcome, _calls = await _finalize(partials=[])

    assert_that(outcome.partial).is_false()
    assert_that(outcome.stopped_reason).is_equal_to("")


def test_synthesis_counts_chunks_that_reviewed_something() -> None:
    """The gate itself: one reviewed chunk runs the pass, none does not."""
    config = ReviewSynthesisConfig(enabled=True)

    assert_that(should_run_synthesis(config=config, chunks_reviewed=0)).is_false()
    assert_that(should_run_synthesis(config=config, chunks_reviewed=1)).is_true()


def _result(*, stopped_reason: str) -> ReviewResult:
    return ReviewResult(
        metadata=ReviewMetadata(
            model="m",
            provider="p",
            context_window=1,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=0,
            files_total=1,
            checklist_items=0,
            stopped_reason=stopped_reason,
        ),
        summary="",
        findings=(),
    )


def test_a_run_that_reviewed_nothing_is_a_stopped_run_and_exits_non_zero() -> None:
    """The result knows it reviewed nothing; the CLI exits 1 on it like a P1."""
    result = _result(stopped_reason=NOTHING_REVIEWED_REASON)

    assert_that(result.reviewed_nothing).is_true()
    assert_that(result.has_p1_findings).is_false()
    assert_that(_result(stopped_reason="").reviewed_nothing).is_false()
    assert_that(_result(stopped_reason="timeout").reviewed_nothing).is_false()
