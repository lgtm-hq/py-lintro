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
from lintro.ai.review.cli_limits import is_cli_output_exhaustion
from lintro.ai.review.context import split_unified_diff_by_file
from lintro.ai.review.coverage import review_eligible_paths
from lintro.ai.review.diff_gate import DiffGateCounts
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.finding_parser import reject_context_findings
from lintro.ai.review.merge import (
    ChunkReviewPartial,
    merge_findings,
)
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.response_pipeline import (
    ChunkCallResult,
    ChunkReviewRequest,
    invoke_chunk_review,
    parse_review_payload_with_recovery,
    payload_to_partial,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "merge_half_partials",
    "review_chunk_main_pass",
    "scope_partial_to_chunk",
    "split_chunk",
]


def split_chunk(*, chunk: ReviewChunk) -> tuple[ReviewChunk, ReviewChunk] | None:
    """Bisect a chunk by file count into two chunks that keep its identity.

    Each half carries the per-file diff sections of its own files, in the
    chunk's file order, and the parent's id, relationship and metadata note,
    so prompts and progress events keep naming the chunk the run planned.

    Args:
        chunk: The chunk to split.

    Returns:
        The two halves, or ``None`` when the chunk has fewer than two files
        and cannot be split.
    """
    if len(chunk.files) < 2:
        return None
    per_file = split_unified_diff_by_file(unified_diff=chunk.diff)
    midpoint = len(chunk.files) // 2
    halves = (list(chunk.files[:midpoint]), list(chunk.files[midpoint:]))
    left, right = (
        ReviewChunk(
            id=chunk.id,
            files=files,
            diff="".join(per_file.get(path, "") for path in files),
            relationship=chunk.relationship,
            metadata_note=chunk.metadata_note,
        )
        for files in halves
    )
    return left, right


def _sum_turns(*, partials: list[ChunkReviewPartial]) -> int | None:
    """Sum the halves' transport-reported turns, or ``None`` if any is unknown.

    Args:
        partials: The halves' partials.

    Returns:
        The total turn count, or ``None`` when a half reported none.
    """
    turns = [partial.turns for partial in partials]
    if any(value is None for value in turns):
        return None
    return sum(value for value in turns if value is not None)


def merge_half_partials(
    *,
    partials: Sequence[ChunkReviewPartial],
) -> ChunkReviewPartial:
    """Fold the partials of a split chunk back into one chunk partial.

    Findings are deduplicated by location; re-read flags, token, cost and
    timing usage are combined.

    Args:
        partials: The halves' partials, in file order.

    Returns:
        One partial standing for the whole chunk.
    """
    ordered = list(partials)
    return ChunkReviewPartial(
        findings=merge_findings(
            findings_groups=[partial.findings for partial in ordered],
        ),
        input_tokens=sum(partial.input_tokens for partial in ordered),
        output_tokens=sum(partial.output_tokens for partial in ordered),
        cost_estimate=sum(partial.cost_estimate for partial in ordered),
        provider_seconds=sum(partial.provider_seconds for partial in ordered),
        context_tokens=sum(partial.context_tokens for partial in ordered),
        turns=_sum_turns(partials=ordered),
        files=tuple(path for partial in ordered for path in partial.files),
        flagged_files=tuple(
            flag for partial in ordered for flag in partial.flagged_files
        ),
        coverage_degradations=tuple(
            item for partial in ordered for item in partial.coverage_degradations
        ),
        diff_gate=sum((partial.diff_gate for partial in ordered), DiffGateCounts()),
    )


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
        chunk=request.chunk,
        provider=request.provider,
        ai_config=request.ai_config,
        budget=request.budget,
        repo_root=request.repo_root,
        use_one_shot=request.use_one_shot,
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
        call = await invoke_chunk_review(request=request)
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
        try:
            call = await invoke_chunk_review(request=half_request)
            partials.append(await _parse_call(request=half_request, call=call))
        except AICostBudgetExceededError:
            raise
        except AIError as exc:
            if position == len(halves) - 1 and not partials:
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
        return await _retry_after_turn_limit(request=request, first=exc)
    except AIError as exc:
        if not is_cli_output_exhaustion(exc):
            raise
        return await _retry_after_exhaustion(request=request)
    return await _parse_call(request=request, call=call)


async def _retry_after_turn_limit(
    *,
    request: ChunkReviewRequest,
    first: AITurnLimitError,
) -> ChunkReviewPartial:
    """Retry a turn-limited call once; degrade the chunk if it hits it again.

    The agent spent its whole turn budget without answering. One unchanged
    retry covers a run that merely wandered; a second limit means the chunk
    does not answer under this bound, so its files are left unreviewed for a
    later round and the run records a ``TURN_LIMIT_REACHED`` degradation
    instead of failing (#2685).

    Args:
        request: The chunk, prompt material, provider handles and limits.
        first: The error the first call raised.

    Returns:
        The retry's parsed partial, or an empty partial carrying the
        degradation and no reviewed files.

    Raises:
        AICostBudgetExceededError: When the retry hits the session cost cap.
    """
    logger.warning(
        "CLI review hit its per-call turn limit on chunk {index} ({error}); "
        "retrying the call once unchanged.",
        index=request.chunk_index,
        error=first,
    )
    try:
        call = await invoke_chunk_review(request=request)
    except AICostBudgetExceededError:
        raise
    except AITurnLimitError as again:
        logger.warning(
            "CLI review hit its per-call turn limit again on chunk {index}; "
            "leaving its files unreviewed for a later round: {error}",
            index=request.chunk_index,
            error=again,
        )
        return ChunkReviewPartial(
            findings=(),
            input_tokens=first.input_tokens + again.input_tokens,
            output_tokens=first.output_tokens + again.output_tokens,
            cost_estimate=first.cost_estimate + again.cost_estimate,
            turns=_add_turns(first.turns, again.turns),
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
    partial = await _parse_call(request=request, call=call)
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


def scope_partial_to_chunk(
    *,
    partial: ChunkReviewPartial,
    request: ChunkReviewRequest,
) -> ChunkReviewPartial:
    """Keep only findings on the chunk's own files; the rest become flags.

    The run-level path gate allows any file in the resume queue, so a chunk
    answering about another queued chunk's file (which the repository context
    section may have shown it, #2714) would otherwise post a finding the
    chunk never had the diff for. Findings on other review-eligible files
    become re-read flags, everything else is dropped, exactly as the run-level
    gate does but with the chunk's file set as the allowed set (#2719).

    Args:
        partial: The parsed chunk partial.
        request: The request that produced it (chunk files, run context).

    Returns:
        The partial with out-of-chunk findings converted or dropped.
    """
    kept, flags = reject_context_findings(
        findings=partial.findings,
        allowed_paths=set(request.chunk.files),
        eligible_paths=set(
            review_eligible_paths(
                changed_files=request.context.changed_files,
                skipped=request.context.skipped_files,
            ),
        ),
    )
    if len(kept) == len(partial.findings) and not flags:
        return partial
    return replace(
        partial,
        findings=kept,
        flagged_files=(*partial.flagged_files, *flags),
    )
