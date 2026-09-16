"""Per-call CLI bounds: turn limit, read-only tools, degradation (#2685)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from assertpy import assert_that
from pydantic import ValidationError

from lintro.ai import cli_bounds
from lintro.ai.cli_bounds import (
    DEFAULT_MAX_TURNS,
    CliCallOptions,
    bound_cli_call,
    current_cli_call_options,
    resolve_max_turns,
)
from lintro.ai.config import AIConfig
from lintro.ai.enums import AICallKind, AITransport
from lintro.ai.exceptions import AIError, AITurnLimitError
from lintro.ai.invoke import call_ai
from lintro.ai.providers.anthropic.metadata import ANTHROPIC_METADATA
from lintro.ai.providers.anthropic.provider import _hit_turn_limit
from lintro.ai.providers.cursor.metadata import CURSOR_METADATA
from lintro.ai.providers.openai.metadata import OPENAI_METADATA
from lintro.ai.providers.response import AIResponse
from lintro.ai.retry import with_retry
from lintro.ai.review.coverage_degradation import describe_coverage_degradations
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.review_metadata import ReviewMetadata

# --- resolution ---------------------------------------------------------------


def test_kind_defaults_and_the_override() -> None:
    """Review-type calls get 3 turns, summary and fix 1; the knob wins over all."""
    assert_that(dict(DEFAULT_MAX_TURNS)).is_equal_to(
        {AICallKind.REVIEW: 3, AICallKind.SUMMARY: 1, AICallKind.FIX: 1},
    )
    for kind, expected in DEFAULT_MAX_TURNS.items():
        assert_that(resolve_max_turns(call_kind=kind, configured=None)).is_equal_to(
            expected,
        )
        assert_that(resolve_max_turns(call_kind=kind, configured=5)).is_equal_to(5)


def test_the_knob_lives_under_the_cli_transport_profile() -> None:
    """``ai.transports.cli.max_turns`` defaults to unset and rejects zero."""
    assert_that(AIConfig().transports.cli.max_turns).is_none()
    config = AIConfig.model_validate(
        {"enabled": True, "transports": {"cli": {"max_turns": 2}}},
    )
    assert_that(config.transports.cli.max_turns).is_equal_to(2)
    with pytest.raises(ValidationError):
        AIConfig.model_validate({"transports": {"cli": {"max_turns": 0}}})


def test_bound_cli_call_scopes_the_options_to_the_block() -> None:
    """The context carries the bounds only for the call inside the block."""
    assert_that(current_cli_call_options()).is_none()
    with bound_cli_call(CliCallOptions(max_turns=2)):
        assert_that(current_cli_call_options()).is_equal_to(CliCallOptions(max_turns=2))
    assert_that(current_cli_call_options()).is_none()


# --- call_ai --------------------------------------------------------------------


class _Recorder:
    """A provider double that records the bounds in force when it was called."""

    model_name = "m"

    def __init__(self) -> None:
        self.seen: list[CliCallOptions | None] = []

    async def complete(self, prompt: str, **kwargs: Any) -> AIResponse:
        self.seen.append(current_cli_call_options())
        return AIResponse(content="ok", model="m", provider="anthropic")


@pytest.mark.parametrize(
    ("kind", "expected"),
    [(AICallKind.REVIEW, 3), (AICallKind.SUMMARY, 1), (AICallKind.FIX, 1)],
)
async def test_call_ai_binds_the_kind_default_on_the_cli_transport(
    kind: AICallKind,
    expected: int,
) -> None:
    """Each call kind reaches the provider with its own turn limit in force."""
    provider = _Recorder()
    await call_ai(
        provider=provider,  # type: ignore[arg-type]
        ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
        user_prompt="p",
        system_prompt=None,
        budget=None,
        call_kind=kind,
    )
    assert_that(provider.seen).is_equal_to([CliCallOptions(max_turns=expected)])


async def test_call_ai_applies_the_configured_limit_to_every_kind() -> None:
    """An explicit ``max_turns`` overrides the per-kind default."""
    provider = _Recorder()
    config = AIConfig.model_validate(
        {"enabled": True, "transport": "cli", "transports": {"cli": {"max_turns": 7}}},
    )
    for kind in AICallKind:
        await call_ai(
            provider=provider,  # type: ignore[arg-type]
            ai_config=config,
            user_prompt="p",
            system_prompt=None,
            budget=None,
            call_kind=kind,
        )
    assert_that(set(provider.seen)).is_equal_to({CliCallOptions(max_turns=7)})


async def test_call_ai_binds_nothing_on_the_api_transport() -> None:
    """An API call has no agent loop to bound; the context stays empty."""
    provider = _Recorder()
    await call_ai(
        provider=provider,  # type: ignore[arg-type]
        ai_config=AIConfig(enabled=True, transport=AITransport.API),
        user_prompt="p",
        system_prompt=None,
        budget=None,
    )
    assert_that(provider.seen).is_equal_to([None])


def test_review_passes_default_to_the_review_kind() -> None:
    """``call_kind`` defaults to review, so every review seam is bounded to 3."""
    import inspect

    assert_that(inspect.signature(call_ai).parameters["call_kind"].default).is_equal_to(
        AICallKind.REVIEW,
    )


# --- provider bounds ------------------------------------------------------------


def test_each_provider_declares_how_it_is_bounded() -> None:
    """Claude renders both flags; codex and cursor are read-only with no turn flag."""
    claude = ANTHROPIC_METADATA.cli_bounds
    assert claude is not None
    assert_that(claude.read_only_args).is_equal_to(("--tools", "Read,Grep,Glob"))
    assert_that(claude.read_only_in_base_argv).is_false()
    assert_that(claude.max_turns_flag).is_equal_to("--max-turns")
    assert_that(claude.max_turns_supported).is_true()
    for metadata, args in (
        (OPENAI_METADATA, ("--sandbox", "read-only")),
        (CURSOR_METADATA, ("--mode", "ask")),
    ):
        bounds = metadata.cli_bounds
        assert bounds is not None
        assert_that(bounds.read_only_args).is_equal_to(args)
        assert_that(bounds.read_only_in_base_argv).is_true()
        assert_that(bounds.max_turns_supported).is_false()
    contract = ANTHROPIC_METADATA.cli_contract
    assert contract is not None
    flags = {flag.flag for flag in contract.optional_flags}
    assert_that(flags).contains("--tools", "--max-turns")


# --- the envelope -----------------------------------------------------------------


def test_the_turn_limit_is_read_from_the_subtype_or_the_turn_count() -> None:
    """The dedicated subtype wins; an error envelope at the limit is the fallback."""
    assert_that(
        _hit_turn_limit(data={"subtype": "error_max_turns"}, max_turns=None),
    ).is_true()
    assert_that(
        _hit_turn_limit(data={"is_error": True, "num_turns": 3}, max_turns=3),
    ).is_true()
    assert_that(
        _hit_turn_limit(data={"is_error": True, "num_turns": 2}, max_turns=3),
    ).is_false()
    # Without a limit sent, an error envelope is an ordinary error.
    assert_that(
        _hit_turn_limit(data={"is_error": True, "num_turns": 3}, max_turns=None),
    ).is_false()
    # A successful answer that used every turn is not a limit hit.
    assert_that(
        _hit_turn_limit(data={"is_error": False, "num_turns": 3}, max_turns=3),
    ).is_false()


# --- retry -------------------------------------------------------------------------


async def test_the_retry_loop_never_repeats_a_turn_limited_call() -> None:
    """The same prompt spends the same turns; the loop raises on the first hit."""
    calls = AsyncMock(side_effect=AITurnLimitError("limit"))
    guarded = with_retry(max_retries=3, base_delay=0.0, max_delay=0.0)(calls)
    with pytest.raises(AITurnLimitError):
        await guarded()
    assert_that(calls.await_count).is_equal_to(1)


# --- the degradation ---------------------------------------------------------------


def _metadata(*reasons: CoverageDegradationReason) -> ReviewMetadata:
    return ReviewMetadata(
        model="m",
        provider="anthropic",
        context_window=1,
        depth=1,
        chunks_total=2,
        chunks_current=2,
        files_reviewed=1,
        files_total=2,
        checklist_items=0,
        coverage_degradations=tuple(
            CoverageDegradation(reason=reason, chunk_index=index)
            for index, reason in enumerate(reasons)
        ),
    )


def test_a_turn_limited_chunk_is_an_incomplete_finding_set() -> None:
    """The reason clears ``findings_coverage_complete`` and is described."""
    metadata = _metadata(CoverageDegradationReason.TURN_LIMIT_REACHED)
    assert_that(metadata.findings_coverage_complete).is_false()
    text = describe_coverage_degradations(metadata=metadata)
    assert_that(text).contains("1 chunk hit the per-call turn limit before answering")
    assert_that(text).contains("its files were left unreviewed")
    two = _metadata(
        CoverageDegradationReason.TURN_LIMIT_REACHED,
        CoverageDegradationReason.TURN_LIMIT_REACHED,
    )
    assert_that(describe_coverage_degradations(metadata=two)).contains(
        "2 chunks hit the per-call turn limit",
    )


async def test_a_chunk_that_hits_the_limit_twice_is_left_unreviewed() -> None:
    """One unchanged retry, then an empty partial that credits no files."""
    from lintro.ai.review import chunk_split_retry

    request = replace(
        _chunk_request(),
        chunk_index=4,
    )
    invoke = AsyncMock(side_effect=[AITurnLimitError("a"), AITurnLimitError("b")])
    with patch.object(chunk_split_retry, "invoke_chunk_review", invoke):
        partial = await chunk_split_retry.review_chunk_main_pass(request=request)

    assert_that(invoke.await_count).is_equal_to(2)
    assert_that(partial.findings).is_empty()
    assert_that(partial.files).is_empty()
    assert_that([d.reason for d in partial.coverage_degradations]).is_equal_to(
        [CoverageDegradationReason.TURN_LIMIT_REACHED],
    )
    assert_that(partial.coverage_degradations[0].chunk_index).is_equal_to(4)


async def test_a_chunk_that_answers_on_the_retry_is_reviewed_normally() -> None:
    """The retry's answer is parsed as if the first call had succeeded."""
    from lintro.ai.review import chunk_split_retry

    request = _chunk_request()
    parsed = object()
    invoke = AsyncMock(side_effect=[AITurnLimitError("a"), "call"])
    with (
        patch.object(chunk_split_retry, "invoke_chunk_review", invoke),
        patch.object(
            chunk_split_retry,
            "_parse_call",
            AsyncMock(return_value=parsed),
        ) as parse,
    ):
        result = await chunk_split_retry.review_chunk_main_pass(request=request)

    assert_that(result).is_same_as(parsed)
    parse.assert_awaited_once()


async def test_another_error_on_the_retry_still_raises() -> None:
    """Only a second turn limit degrades; any other failure is the caller's."""
    from lintro.ai.review import chunk_split_retry

    invoke = AsyncMock(side_effect=[AITurnLimitError("a"), AIError("boom")])
    with (
        patch.object(chunk_split_retry, "invoke_chunk_review", invoke),
        pytest.raises(AIError, match="boom"),
    ):
        await chunk_split_retry.review_chunk_main_pass(request=_chunk_request())


def _chunk_request() -> Any:
    """Build the smallest ChunkReviewRequest the main pass accepts."""
    from lintro.ai.review.group_labels import REL_SINGLE_FILE
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.response_pipeline import ChunkReviewRequest

    context = ReviewContext(
        base_ref="main",
        head_ref="HEAD",
        changed_files=[],
        unified_diff="",
        pr_metadata=None,
    )
    fields = {f.name for f in ChunkReviewRequest.__dataclass_fields__.values()}
    kwargs: dict[str, Any] = {
        "chunk": ReviewChunk(
            id=1,
            files=["a.py"],
            diff="",
            relationship=REL_SINGLE_FILE,
        ),
        "context": context,
        "provider": object(),
        "ai_config": AIConfig(enabled=True, transport=AITransport.CLI),
        "checklist_text": "",
        "checklist_count": 0,
        "interaction_paths": "",
        "lint_results": None,
        "extra_checklist": None,
        "strictness_section": "",
        "budget": None,
        "repo_root": "",
        "use_one_shot": True,
        "diff_budget": 1,
        "chunk_index": 0,
    }
    return ChunkReviewRequest(**{k: v for k, v in kwargs.items() if k in fields})


# --- the context variable is per task -----------------------------------------------


async def test_parallel_calls_each_see_their_own_bounds() -> None:
    """Context variables follow tasks, so parallel chunks never share a limit."""
    import asyncio

    seen: dict[int, CliCallOptions | None] = {}

    async def _one(limit: int) -> None:
        with bound_cli_call(CliCallOptions(max_turns=limit)):
            await asyncio.sleep(0)
            seen[limit] = cli_bounds.current_cli_call_options()

    await asyncio.gather(_one(1), _one(2), _one(3))
    assert_that(seen).is_equal_to(
        {n: CliCallOptions(max_turns=n) for n in (1, 2, 3)},
    )
