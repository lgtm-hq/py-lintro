"""Split-and-retry for a chunk whose answer exhausted the output ceiling.

A CLI transport loses the whole call when one JSON document crosses the
provider's ~32k output-token limit (#1967). The old answer was to retry the
chunk under a tighter per-call findings cap, which silently traded coverage
for a complete object. The cap is gone (lintro-ops milestone 0, decision A):
the answer now is to make the unit of work smaller instead of the answer.

:func:`review_chunk_main_pass` is the seam :mod:`lintro.ai.review.chunk_pass`
calls for the main provider round-trip. On output exhaustion it bisects the
chunk by file count into two halves, reviews each half once, and merges the
two partials under the original chunk index. A single-file chunk cannot be
split, so it is retried once unchanged. Either way the run records one
:attr:`~lintro.ai.review.enums.coverage_degradation_reason.CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED`
degradation for the chunk, because the model never saw the whole chunk in one
view. A second exhaustion propagates as the provider error it is only when
nothing survives it — both halves failing, or the single-file retry. When one
half fails and the other completed, the surviving findings are kept and the
chunk records ``SPLIT_HALF_FAILED`` for the files left unreviewed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.cli_bounds import resolve_max_turns
from lintro.ai.enums.ai_call_kind import AICallKind
from lintro.ai.exceptions import (
    AICostBudgetExceededError,
    AIError,
    AITurnLimitError,
)
from lintro.ai.review.chunk_halves import (
    merge_half_partials,
    scope_partial_to_chunk,
    split_chunk,
)
from lintro.ai.review.cli_limits import is_cli_output_exhaustion
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.response_pipeline import (
    ChunkCallResult,
    ChunkReviewRequest,
    invoke_chunk_review,
    parse_review_payload_with_recovery,
    payload_to_partial,
)

if TYPE_CHECKING:
    pass

__all__ = [
    "merge_half_partials",
    "review_chunk_main_pass",
    "scope_partial_to_chunk",
    "split_chunk",
]


async def _parse_call(
    *,
    request: ChunkReviewRequest,
    call: ChunkCallResult,
) -> ChunkReviewPartial:
    """Parse one main-call answer into a chunk partial.

    Args:
        request: The request the call answered.
        call: The provider response and its wall time.

    Returns:
        The parsed chunk partial.
    """
    response, payload = await parse_review_payload_with_recovery(
        response=call.response,
        request=request,
        elapsed=call.elapsed,
    )
    partial = payload_to_partial(
        response=response,
        payload=payload,
        chunk=request.chunk,
        near_lines=request.ai_config.review_diff_gate_lines,
    )
    partial = scope_partial_to_chunk(partial=partial, request=request)
    # The files this answer actually covered (a half carries only its own),
    # and the call's own wall time for the per-chunk timings.
    return replace(
        partial,
        files=tuple(request.chunk.files),
        provider_seconds=call.elapsed,
        context_tokens=call.context_tokens,
        coverage_degradations=(
            *partial.coverage_degradations,
            *call.coverage_degradations,
        ),
    )


async def _retry_after_exhaustion(
    *,
    request: ChunkReviewRequest,
) -> ChunkReviewPartial:
    """Review a chunk again after its answer exhausted the output ceiling.

    Args:
        request: The request whose first call exhausted the ceiling.

    A single-file retry that fails again raises the provider error it hit.
    When one half fails for any reason but a cost-cap stop, the other half's
    partial is kept and the failed half's files are left out of ``files`` so
    they count as unreviewed; only when both halves fail is the error raised.

    Returns:
        The chunk partial, carrying one output-exhaustion degradation (and a
        split-half-failed degradation when one half was lost).

    Raises:
        AICostBudgetExceededError: When any call hits the session cost cap.
        AIError: When every half failed, or the single-file retry failed
            again.
    """
    degradation = CoverageDegradation(
        reason=CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
        chunk_index=request.chunk_index,
    )
    halves = split_chunk(chunk=request.chunk)
    if halves is None:
        logger.warning(
            "CLI review hit an output-token ceiling on a single-file chunk "
            f"({request.chunk.files[0] if request.chunk.files else '?'}); "
            "retrying the call once unchanged.",
        )
        try:
            call = await invoke_chunk_review(request=request)
        except AITurnLimitError as limit:
            # Rows 5 / 6: the unchanged retry hit the turn limit; the one
            # retry function decides whether a single-shot retry remains.
            partial = await _retry_after_turn_limit(
                request=request,
                first=limit,
                may_split=True,
            )
        else:
            partial = await _parse_call(request=request, call=call)
        return replace(
            partial,
            coverage_degradations=(
                *partial.coverage_degradations,
                # Nothing was split: the chunk kept its whole-file view and
                # was simply asked again, so the run must not report it as a
                # chunk that lost that view.
                replace(degradation, split=False),
            ),
        )

    logger.warning(
        "CLI review hit an output-token ceiling; splitting the "
        f"{len(request.chunk.files)}-file chunk into halves of "
        f"{len(halves[0].files)} and {len(halves[1].files)} files and "
        "reviewing each once.",
    )
    partials: list[ChunkReviewPartial] = []
    lost_half = False
    for position, half in enumerate(halves):
        half_request = replace(request, chunk=half)
        billed: AITurnLimitError | None = None
        try:
            try:
                call = await invoke_chunk_review(request=half_request)
            except AITurnLimitError as limit:
                # Row 7: the half takes the one retry function at half level;
                # rows 3 and 8 are decided inside it, not here.
                billed = limit
                partials.append(
                    await _retry_after_turn_limit(
                        request=half_request,
                        first=limit,
                        may_split=False,
                    ),
                )
                continue
            partials.append(await _parse_call(request=half_request, call=call))
        except AICostBudgetExceededError:
            raise
        except AIError as exc:
            if billed is not None:
                # The limited first attempt was charged to the budget; keep
                # its usage on an accounting-only half so the chunk's totals
                # match what was spent (it reviews no file).
                partials.append(
                    _turn_limited_partial(
                        request=half_request,
                        first=billed,
                        again=None,
                    ),
                )
            if position == len(halves) - 1 and not any(p.files for p in partials):
                # Nothing survived: there is no coverage to keep.
                raise
            # The other half's findings are paid for and complete; losing
            # them to this half's failure would discard real coverage. The
            # failed half's files stay out of ``files`` so coverage crediting
            # reports them unreviewed rather than reviewed.
            logger.warning(
                "One half of a split chunk failed ({files}); keeping the "
                "other half's findings and leaving those files unreviewed: "
                "{error}",
                files=", ".join(half.files),
                error=exc,
            )
            lost_half = True
    merged = merge_half_partials(partials=partials)
    half_failed: tuple[CoverageDegradation, ...] = ()
    if lost_half:
        # Named separately from the split itself so the run can say that
        # one half's files were not reviewed rather than "re-reviewed in
        # halves", and so the coverage sentence never claims every chunk.
        half_failed = (
            CoverageDegradation(
                reason=CoverageDegradationReason.SPLIT_HALF_FAILED,
                chunk_index=request.chunk_index,
            ),
        )
    return replace(
        merged,
        coverage_degradations=(
            *merged.coverage_degradations,
            degradation,
            *half_failed,
        ),
    )


async def review_chunk_main_pass(
    *,
    request: ChunkReviewRequest,
) -> ChunkReviewPartial:
    """Run a chunk's main provider call and parse its answer.

    Args:
        request: The chunk, prompt material, provider handles and limits.

    Returns:
        The chunk's main-pass partial. When the first call exhausted the
        provider's output ceiling, the partial is the merge of the chunk's two
        halves (or the single-file retry) and carries one
        ``OUTPUT_EXHAUSTION_RETRIED`` degradation.

    Raises:
        AICostBudgetExceededError: When the session cost ceiling is hit.
        AIError: When the provider call fails for a non-recoverable reason.
    """
    try:
        call = await invoke_chunk_review(request=request)
    except AICostBudgetExceededError:
        raise
    except AITurnLimitError as exc:
        return await _retry_after_turn_limit(request=request, first=exc, may_split=True)
    except AIError as exc:
        if not is_cli_output_exhaustion(exc):
            raise
        return await _retry_after_exhaustion(request=request)
    return await _parse_call(request=request, call=call)


def _turn_limited_partial(
    *,
    request: ChunkReviewRequest,
    first: AITurnLimitError,
    again: AITurnLimitError | None,
) -> ChunkReviewPartial:
    """Build the partial for a chunk left unreviewed by the turn limit.

    Args:
        request: The chunk's request.
        first: The limit the first attempt hit.
        again: The limit the retry hit, or ``None`` when the limit ended a
            path that had no retry left.

    Returns:
        An empty partial with no files, carrying both attempts' usage and
        one ``TURN_LIMIT_REACHED`` degradation.
    """
    logger.warning(
        "CLI review hit its per-call turn limit again on chunk {index}; "
        "leaving its files unreviewed for a later round.",
        index=request.chunk_index,
    )
    extra = again if again is not None else AITurnLimitError("")
    return ChunkReviewPartial(
        findings=(),
        input_tokens=first.input_tokens + extra.input_tokens,
        output_tokens=first.output_tokens + extra.output_tokens,
        cost_estimate=first.cost_estimate + extra.cost_estimate,
        turns=_add_turns(first.turns, extra.turns),
        files=(),
        coverage_degradations=(
            CoverageDegradation(
                reason=CoverageDegradationReason.TURN_LIMIT_REACHED,
                chunk_index=request.chunk_index,
                limit=resolve_max_turns(
                    call_kind=AICallKind.REVIEW,
                    configured=request.ai_config.transports.cli.max_turns,
                ),
            ),
        ),
    )


async def _retry_after_turn_limit(
    *,
    request: ChunkReviewRequest,
    first: AITurnLimitError,
    may_split: bool,
) -> ChunkReviewPartial:
    """Retry a turn-limited call once, single-shot; degrade if it fails again.

    The bounded-recovery table (#2731; every row narrows the state — the
    attempt becomes single-shot or the chunk halves — so it terminates):

    ==== ========================= ============ ==================================
    row  attempt                   outcome      next / recorded
    ==== ========================= ============ ==================================
    1    whole chunk, tools        turn limit   single-shot retry (row 3/4)
    2    whole chunk, tools        exhaustion   split in halves (rows 7+), or a
                                                single file: unchanged retry
                                                (row 5); ``OUTPUT_EXHAUSTION_RETRIED``
    3    whole chunk, single-shot  turn limit   done: ``TURN_LIMIT_REACHED``, no files
    4    whole chunk, single-shot  exhaustion   split in halves, halves stay
                                                single-shot (rows 7+)
    5    single file, unchanged    turn limit   single-shot retry if not yet
                                                single-shot (row 3/4), else done
                                                as row 3
    6    single file, single-shot  turn limit   done as row 3
    7    half, tools               turn limit   that half: single-shot retry
                                                (rows 3/4 on the half)
    8    half, any                 exhaustion   that half is lost:
                                                ``SPLIT_HALF_FAILED``; the other
                                                half's files are kept
    9    any                       cost cap     raised, the run stops
    10   any                       other error  whole chunk: raised; a half: lost
                                                as row 8
    ==== ========================= ============ ==================================

    The agent spent its whole turn budget without answering. The retry is
    not the same call again (#2731: on a one-file PR the same prompt did
    the same reading and hit the same limit): it keeps the turn limit but
    drops the generated questions from the prompt and takes the agent's
    tools away, so the second attempt answers from the diff, the context
    section and the rubric in one turn. A second limit means the chunk
    does not answer under this bound, so its files are left unreviewed for
    a later round and the run records a ``TURN_LIMIT_REACHED`` degradation
    instead of failing (#2685).

    Args:
        request: The chunk, prompt material, provider handles and limits.
        first: The error the first call raised.
        may_split: True at the whole-chunk level, where an exhausted retry
            splits (row 4); False for a half, where it is the half's loss
            (row 8) and the error is re-raised to the half-loss handler.
            This is the one place rows 3 and 8 are decided for both levels.

    Returns:
        The retry's parsed partial, or an empty partial carrying the
        degradation and no reviewed files.

    Raises:
        AICostBudgetExceededError: When the retry hits the session cost cap.
        AIError: When the retry fails for a reason that is neither a turn
            limit nor output exhaustion.
    """
    logger.warning(
        "CLI review hit its per-call turn limit on chunk {index} ({error}); "
        "retrying once single-shot (no questions, no tools).",
        index=request.chunk_index,
        error=first,
    )
    if request.single_shot:
        # Row 3 (and 6): the limit hit an attempt that was already
        # single-shot; there is no narrower retry, the chunk is done.
        return _turn_limited_partial(request=request, first=first, again=None)
    retry = replace(request, single_shot=True)
    try:
        call = await invoke_chunk_review(request=retry)
    except AICostBudgetExceededError:
        raise
    except AIError as exc:
        if not isinstance(exc, AITurnLimitError):
            if not is_cli_output_exhaustion(exc) or not may_split:
                # Row 8 on a half (or any other error): the caller's loss
                # handler takes it, with the first attempt's usage.
                raise
            # Row 4: the single-shot answer overran the output ceiling; split
            # as the first attempt would have, keeping the single-shot shape.
            partial = await _retry_after_exhaustion(request=retry)
            return replace(
                partial,
                input_tokens=partial.input_tokens + first.input_tokens,
                output_tokens=partial.output_tokens + first.output_tokens,
                cost_estimate=partial.cost_estimate + first.cost_estimate,
                turns=_add_turns(partial.turns, first.turns),
            )
        return _turn_limited_partial(request=request, first=first, again=exc)
    partial = await _parse_call(request=retry, call=call)
    # The stopped first attempt was billed too; the chunk reports both.
    return replace(
        partial,
        input_tokens=partial.input_tokens + first.input_tokens,
        output_tokens=partial.output_tokens + first.output_tokens,
        cost_estimate=partial.cost_estimate + first.cost_estimate,
        turns=_add_turns(partial.turns, first.turns),
    )


def _add_turns(*counts: int | None) -> int | None:
    """Add reported turn counts, keeping ``None`` when none was reported.

    Args:
        *counts: Per-attempt turn counts, ``None`` where the transport gave none.

    Returns:
        The sum of the known counts, or ``None`` when every count is unknown.
    """
    known = [count for count in counts if count is not None]
    return sum(known) if known else None
