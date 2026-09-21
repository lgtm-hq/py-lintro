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

from lintro.ai.cli_bounds import CliCallOptions, current_cli_call_options
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AITurnLimitError
from lintro.ai.invoke import call_ai
from lintro.ai.providers.response import AIResponse
from lintro.ai.review import chunk_split_retry, provider_call, response_pipeline
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.changed_file import ChangedFile
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
