"""The depth-controlled review of a single chunk (issue #2301).

One chunk, up to three provider calls: the optional depth-2 question
generator, the main review call, and the optional depth-3 adversarial sweep.
:func:`review_chunk_with_progress` wraps that in the run's progress events and
the #1101 error taxonomy, so the fan-out above only has to schedule.
:func:`review_chunk` is the seam between the fan-out in
:mod:`lintro.ai.review.chunk_runner`, which decides *when* a chunk runs, and
the passes in :mod:`lintro.ai.review.response_pipeline`,
:mod:`lintro.ai.review.checklist_pass` and
:mod:`lintro.ai.review.adversarial_pass`, which decide what each call asks.

Every provider call below goes through
:mod:`lintro.ai.review.provider_call`, the single seam tests replace.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport
from lintro.ai.review.adversarial_pass import run_adversarial_pass
from lintro.ai.review.checklist_pass import generate_extra_checklist
from lintro.ai.review.cli_limits import resolve_cli_findings_cap
from lintro.ai.review.merge import ChunkReviewPartial, merge_findings
from lintro.ai.review.paths_registry import generate_interaction_paths
from lintro.ai.review.progress import NullReviewProgress, StepTrackingProgress
from lintro.ai.review.response_pipeline import (
    ChunkReviewRequest,
    invoke_chunk_review,
    parse_review_payload_with_recovery,
    payload_to_partial,
)
from lintro.ai.review.session import aborted_before_completion, is_cost_cap_stop
from lintro.ai.review.timings import ReviewPhase, ReviewTimingRecorder

if TYPE_CHECKING:
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.session import ChunkRunPlan

__all__ = ["review_chunk", "review_chunk_with_progress"]


async def review_chunk(
    *,
    chunk: ReviewChunk,
    chunk_index: int = 0,
    plan: ChunkRunPlan,
) -> tuple[ChunkReviewPartial, int]:
    """Run depth-controlled review for a single chunk.

    Args:
        chunk: The chunk to review.
        chunk_index: Position of the chunk in the run.
        plan: Run-scope inputs, already specialised for this chunk.

    Returns:
        The chunk partial and the next available generated checklist id.
    """
    recorder = plan.timings or ReviewTimingRecorder()
    tracker = plan.progress or NullReviewProgress()
    ai_config = plan.ai_config
    next_generated_checklist_id = plan.next_generated_checklist_id
    interaction_paths = generate_interaction_paths(
        classifications=plan.classifications,
        changed_files=chunk.files,
    )
    extra_checklist = ""
    extra_checklist_usage: ChunkReviewPartial | None = None
    if plan.depth >= 2:
        tracker.on_step(chunk_index=chunk_index, step="generating questions")
        with recorder.phase(name=ReviewPhase.GENERATED_QUESTIONS):
            (
                extra_checklist,
                next_generated_checklist_id,
                extra_checklist_usage,
            ) = await generate_extra_checklist(
                chunk=chunk,
                context=plan.context,
                provider=plan.provider,
                ai_config=ai_config,
                budget=plan.budget,
                next_generated_checklist_id=next_generated_checklist_id,
                repo_root=plan.repo_root,
                use_one_shot=plan.use_one_shot,
            )

    tracker.on_step(chunk_index=chunk_index, step="reviewing")
    # Gate before the main provider call so intra-chunk (depth-2/3) work
    # cannot overshoot the budget between the per-chunk checks.
    plan.budget.check()
    response, elapsed, chunk_degradations = await invoke_chunk_review(
        request=ChunkReviewRequest(
            chunk=chunk,
            context=plan.context,
            provider=plan.provider,
            ai_config=ai_config,
            checklist_text=plan.checklist_text,
            checklist_count=len(plan.checklist_items),
            interaction_paths=interaction_paths,
            lint_results=plan.lint_results,
            extra_checklist=extra_checklist,
            strictness_section=plan.strictness_section,
            budget=plan.budget,
            repo_root=plan.repo_root,
            use_one_shot=plan.use_one_shot,
            diff_budget=plan.diff_budget,
            max_findings=resolve_cli_findings_cap(
                transport_is_cli=ai_config.transport == AITransport.CLI,
                cli_max_findings_per_call=ai_config.cli_max_findings_per_call,
            ),
            chunk_index=chunk_index,
        ),
    )
    response, payload = await parse_review_payload_with_recovery(
        response=response,
        chunk=chunk,
        provider=plan.provider,
        ai_config=ai_config,
        budget=plan.budget,
        repo_root=plan.repo_root,
        use_one_shot=plan.use_one_shot,
        elapsed=elapsed,
    )
    partial = replace(
        payload_to_partial(response=response, payload=payload),
        files=tuple(chunk.files),
        coverage_degradations=chunk_degradations,
    )

    if extra_checklist_usage is not None:
        partial = _add_usage(partial=partial, extra=extra_checklist_usage)

    if plan.depth >= 3:
        tracker.on_step(chunk_index=chunk_index, step="adversarial sweep")
        with recorder.phase(name=ReviewPhase.ADVERSARIAL):
            adversarial = await run_adversarial_pass(
                chunk=chunk,
                provider=plan.provider,
                ai_config=ai_config,
                prior_findings=partial.findings,
                budget=plan.budget,
                repo_root=plan.repo_root,
                use_one_shot=plan.use_one_shot,
            )
        partial = replace(
            _add_usage(partial=partial, extra=adversarial),
            findings=merge_findings(
                findings_groups=[partial.findings, adversarial.findings],
            ),
        )

    return partial, next_generated_checklist_id


def _add_usage(
    *,
    partial: ChunkReviewPartial,
    extra: ChunkReviewPartial,
) -> ChunkReviewPartial:
    """Fold a supplementary pass's token and cost usage into a chunk partial.

    Args:
        partial: The chunk partial to add usage to.
        extra: The supplementary pass whose usage is folded in.

    Returns:
        The partial with the combined usage totals.
    """
    return replace(
        partial,
        input_tokens=partial.input_tokens + extra.input_tokens,
        output_tokens=partial.output_tokens + extra.output_tokens,
        cost_estimate=partial.cost_estimate + extra.cost_estimate,
    )


async def review_chunk_with_progress(
    *,
    chunk_index: int,
    chunk: ReviewChunk,
    total_chunks: int,
    plan: ChunkRunPlan,
) -> ChunkReviewPartial:
    """Review one chunk with progress tracking and error wrapping.

    ``chunk`` is the chunk at position ``chunk_index`` of ``total_chunks``, and
    ``plan`` carries the run-scope inputs already specialised for it. Returns
    the completed chunk partial. ``plan.timings`` records the chunk's
    intra-chunk phase spans (#2148).

    A cost-cap stop (``AICostBudgetExceededError``) is re-raised raw so the run
    can finalize a partial review; any other failure is re-raised wrapped as a
    ``ReviewExecutionError`` by :func:`aborted_before_completion`, after the
    progress tracker is notified. Both are raised from values built elsewhere,
    so the sections above stay prose.
    """
    progress = plan.progress
    plan.budget.check()
    progress.on_chunk_start(chunk_index=chunk_index, files=list(chunk.files))
    try:
        partial, _next_id = await review_chunk(
            chunk=chunk,
            chunk_index=chunk_index,
            plan=plan,
        )
    except Exception as exc:
        # A cost-cap stop is an expected graceful halt, not a chunk failure:
        # re-raise it raw so run_review can finalize a partial cleanly.
        if is_cost_cap_stop(exc=exc):
            raise
        step_tracker = progress if isinstance(progress, StepTrackingProgress) else None
        last_step = step_tracker.last_step if step_tracker else "reviewing"
        progress.on_error(
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            step=last_step,
            completed_chunks=chunk_index,
            error=exc,
        )
        raise aborted_before_completion(
            cause=exc,
            provider=plan.provider,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            step=last_step,
            completed_chunks=chunk_index,
        ) from exc
    progress.on_chunk_done(chunk_index=chunk_index)
    return partial
