"""Provider calls for one review run, and how the run is finalized (#2301).

Split out of :mod:`lintro.ai.review.orchestrator`, which keeps the public
facade and the three-step sequence. Everything between "the plan is resolved"
and "the outcome is assembled" lives here: the chunk fan-out, the custom-agent
passes, the merge, the optional synthesis pass, and the two finalizers — one
for a completed run, one for a run a cost cap, timeout or SIGTERM stopped.

Every function was moved verbatim, so the run's behaviour, its timings and its
graceful-stop handling are unchanged. See
``docs/architecture/AI-REVIEW-EXECUTION.md``.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIError
from lintro.ai.review.checklist_pass import max_checklist_id
from lintro.ai.review.chunk_runner import review_all_chunks
from lintro.ai.review.custom_agent_runner import run_custom_agent_passes
from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.incremental_coverage import checkpoint_writer
from lintro.ai.review.interrupt import install_review_interrupt
from lintro.ai.review.merge import finalize_partials
from lintro.ai.review.result_assembly import (
    ReviewRunOutcome,
    chunk_summaries,
)
from lintro.ai.review.session import (
    ChunkRunPlan,
    cost_cap_reason,
    is_cost_cap_stop,
    is_timeout_stop,
    timeout_reason,
)
from lintro.ai.review.synthesis import run_synthesis_pass, should_run_synthesis
from lintro.ai.review.timings import ReviewPhase

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.review.custom_agent_runner import CustomAgentPassResult
    from lintro.ai.review.merge import ChunkReviewPartial
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.run_planning import ReviewRunPlan
    from lintro.ai.review.session import ReviewSessionOptions

__all__ = [
    "RunProgress",
    "chunk_run_plan",
    "execute_run",
    "finalize_completed_run",
    "finalize_stopped_run",
    "merge_partials",
    "run_passes",
    "stop_hint",
]


def chunk_run_plan(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    plan: ReviewRunPlan,
    interrupt: asyncio.Event,
) -> ChunkRunPlan:
    """Narrow the run plan to what the chunk fan-out needs.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        plan: The resolved run plan.
        interrupt: Event a SIGTERM/SIGINT handler sets to stop the run.

    Returns:
        The per-run chunk plan every chunk layer reads.
    """
    return ChunkRunPlan(
        context=context,
        provider=options.provider,
        ai_config=plan.ai_config,
        depth=options.depth,
        checklist_items=options.checklist_items,
        checklist_text=options.checklist_text,
        classifications=options.classifications,
        lint_results=options.lint_results,
        budget=plan.budget,
        progress=plan.tracker,
        repo_root=plan.repo_root,
        use_one_shot=plan.use_one_shot,
        strictness_section=plan.strictness_section,
        next_generated_checklist_id=(
            max_checklist_id(checklist_items=options.checklist_items) + 1
        ),
        diff_budget=plan.diff_budget,
        max_parallel_calls=plan.max_parallel_calls,
        stop=interrupt,
        timings=plan.timings,
    )


@dataclass(slots=True)
class RunProgress:
    """Work a run has finished, readable after a graceful stop.

    The chunk fan-out and the custom-agent runner append to these as they go,
    so an aborted run still reports what it completed.

    Attributes:
        collected: Chunk partials completed so far, in completion order.
        custom_results: Custom-agent passes that completed.
        custom_agents_failed: Names of selected agents that produced no pass.
    """

    collected: list[ChunkReviewPartial] = field(default_factory=list)
    custom_results: list[CustomAgentPassResult] = field(default_factory=list)
    custom_agents_failed: list[str] = field(default_factory=list)


async def run_passes(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    plan: ReviewRunPlan,
    progress: RunProgress,
    interrupt: asyncio.Event,
) -> list[ChunkReviewPartial]:
    """Run the chunk fan-out and then the scoped custom-agent passes.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        plan: The resolved run plan.
        progress: Accumulator the completed work is appended to.
        interrupt: Event a SIGTERM/SIGINT handler sets to stop the run.

    Returns:
        The completed chunk partials, in chunk order.
    """
    partials: list[ChunkReviewPartial] = []
    if plan.chunks:
        partials = await review_all_chunks(
            chunks=plan.chunks,
            plan=chunk_run_plan(
                context=context,
                options=options,
                plan=plan,
                interrupt=interrupt,
            ),
            completed_sink=progress.collected,
            on_chunk_complete=checkpoint_writer(
                resume=plan.resume,
                context=context,
                prior_state=options.prior_state,
                force_full=options.force_full,
                policy=plan.policy,
            ),
        )
    if plan.resume.queue:
        await run_custom_agent_passes(
            selected=plan.agent_selection.selected,
            context=context,
            provider=options.provider,
            ai_config=plan.ai_config,
            budget=plan.budget,
            repo_root=plan.repo_root,
            workspace_root=options.workspace_root,
            # Never reuse the built-in review's durable session: each agent
            # is an independent, narrowly scoped pass with its own
            # instructions.
            use_one_shot=True,
            on_pass_complete=progress.custom_results.append,
            on_agent_failed=progress.custom_agents_failed.append,
        )
    return partials


def merge_partials(
    *,
    plan: ReviewRunPlan,
    progress: RunProgress,
    partials: list[ChunkReviewPartial],
) -> ReviewRunOutcome:
    """Merge the run's partials and fold in the custom-agent findings.

    Custom agent findings bypass the run-level sensitivity filter: each agent
    declares its own strictness and severity policy, so a run-level preset must
    not silently drop what a maintainer explicitly asked to be checked.

    Args:
        plan: The resolved run plan.
        progress: The work the run completed.
        partials: The chunk partials to merge.

    Returns:
        An outcome carrying the merged result and the filtered findings; the
        timing and stop fields are filled in by the caller.
    """
    merged, filtered_findings, _count = finalize_partials(
        partials=partials,
        policy=plan.policy,
    )
    custom_findings = tuple(
        finding for result in progress.custom_results for finding in result.findings
    )
    filtered_findings = filtered_findings + custom_findings
    return ReviewRunOutcome(
        partials=partials,
        custom_results=progress.custom_results,
        custom_agents_failed=progress.custom_agents_failed,
        merged=merged,
        filtered_findings=filtered_findings,
        custom_findings=custom_findings,
        total_findings=len(filtered_findings),
    )


async def finalize_completed_run(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    plan: ReviewRunPlan,
    progress: RunProgress,
    partials: list[ChunkReviewPartial],
    provider_seconds: float,
    interrupt: asyncio.Event,
) -> ReviewRunOutcome:
    """Merge a completed run and run the optional cross-chunk synthesis pass.

    The synthesis seam (#2269) is the one place the optional whole-PR pass
    hooks in: after the chunk findings are merged and filtered, before the
    result is assembled. Everything the pass does lives in
    :mod:`lintro.ai.review.synthesis`, so #1972 Phase 4 can move this call
    without touching the pass itself. Only the completed path runs it: a review
    already stopped by a cost cap or a timeout must not spend another call.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        plan: The resolved run plan.
        progress: The work the run completed.
        partials: The chunk partials to merge.
        provider_seconds: Seconds the provider phase took, already recorded.
        interrupt: Event a SIGTERM/SIGINT handler sets to stop the run.

    Returns:
        The outcome of the completed run.
    """
    merge_started = time.monotonic()
    outcome = merge_partials(plan=plan, progress=progress, partials=partials)
    parse_merge_seconds = time.monotonic() - merge_started
    plan.timings.add_phase(
        name=ReviewPhase.PARSE_MERGE,
        seconds=parse_merge_seconds,
    )
    outcome = replace(
        outcome,
        provider_seconds=provider_seconds,
        parse_merge_seconds=parse_merge_seconds,
    )
    if not should_run_synthesis(
        config=options.synthesis,
        chunks_reviewed=len(partials),
    ):
        return outcome
    # ``should_run_synthesis`` already rejected a None config; bind it so the
    # type checker knows that too.
    synthesis_config = options.synthesis
    assert synthesis_config is not None
    with plan.timings.phase(name=ReviewPhase.SYNTHESIS):
        synthesis_pass = await run_synthesis_pass(
            context=context,
            summaries=chunk_summaries(chunks=plan.chunks, partials=partials),
            existing_findings=outcome.filtered_findings,
            provider=options.provider,
            ai_config=plan.ai_config,
            config=synthesis_config,
            policy=plan.policy,
            budget=plan.budget,
            repo_root=plan.repo_root,
            # Never reuse the built-in review's durable session: the pass is a
            # standalone whole-PR question, not a chunk.
            use_one_shot=True,
            diff_budget=plan.diff_budget,
            # The chunk fan-out already raced this event so a SIGTERM can
            # persist coverage inside the runner's shutdown window; the extra
            # call gets the same treatment, and a stop that lands here is
            # recorded as a failed pass.
            stop=interrupt,
        )
    findings = outcome.filtered_findings + synthesis_pass.findings
    return replace(
        outcome,
        synthesis_pass=synthesis_pass,
        filtered_findings=findings,
        total_findings=len(findings),
    )


def stop_hint(*, stopped_reason: str, ai_config: AIConfig) -> str:
    """Describe how to get the rest of a stopped review reviewed.

    Args:
        stopped_reason: The graceful stop that ended the run.
        ai_config: AI configuration the run used.

    Returns:
        A one-sentence operator hint.
    """
    if "SIGTERM" in stopped_reason:
        return (
            "The runner sent SIGTERM; coverage was persisted. "
            "Re-run to resume remaining files."
        )
    if stopped_reason.startswith("timeout"):
        timeout_setting = (
            "ai.transports.cli.timeout"
            if ai_config.transport is AITransport.CLI
            else "ai.transports.api.timeout"
        )
        return f"Raise {timeout_setting} or narrow --path to review the rest."
    return "Raise ai.max_cost_usd or narrow --path to review the rest."


def finalize_stopped_run(
    *,
    plan: ReviewRunPlan,
    progress: RunProgress,
    exc: Exception,
    stopped_reason: str,
    provider_started: float,
    provider_seconds: float,
) -> ReviewRunOutcome:
    """Finalize a run a cost cap, timeout or SIGTERM stopped mid-way.

    Keeps the chunks reviewed so far instead of discarding completed work
    (#1094 / #2154). When the stop trips before any chunk completes the partial
    is empty-but-actionable rather than a generic abort.

    Args:
        plan: The resolved run plan.
        progress: The work the run completed before the stop.
        exc: The exception that stopped the run.
        stopped_reason: The graceful stop the exception was classified as.
        provider_started: Monotonic timestamp the provider phase opened at.
        provider_seconds: Provider seconds already recorded, or ``0.0`` when
            the stop landed before the phase was closed.

    Returns:
        The outcome of the stopped run.
    """
    if provider_seconds <= 0.0:
        provider_seconds = time.monotonic() - provider_started
        plan.timings.add_phase(name=ReviewPhase.PROVIDER, seconds=provider_seconds)
    partials = list(progress.collected)
    merge_started = time.monotonic()
    outcome = merge_partials(plan=plan, progress=progress, partials=partials)
    parse_merge_seconds = time.monotonic() - merge_started
    plan.timings.add_phase(
        name=ReviewPhase.PARSE_MERGE,
        seconds=parse_merge_seconds,
    )
    logger.warning(
        "Review stopped early — {reason} after reviewing {n} of {m} chunks. {hint}",
        reason=stopped_reason,
        hint=stop_hint(stopped_reason=stopped_reason, ai_config=plan.ai_config),
        n=len(partials),
        m=len(plan.chunks),
        cause=str(exc),
    )
    return replace(
        outcome,
        stopped_reason=stopped_reason,
        partial=True,
        provider_seconds=provider_seconds,
        parse_merge_seconds=parse_merge_seconds,
    )


async def execute_run(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    plan: ReviewRunPlan,
) -> ReviewRunOutcome:
    """Make the run's provider calls and finalize however it ends.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        plan: The resolved run plan.

    Returns:
        The outcome of the run, completed or gracefully stopped.

    Raises:
        AIError: When the review fails for a non-recoverable reason.
        ReviewExecutionError: When a chunk fails mid-run for a reason other
            than a graceful stop.
    """
    progress = RunProgress()
    completed = False
    durable_session_started = False
    outcome = ReviewRunOutcome()
    provider_seconds = 0.0
    interrupt = options.stop if options.stop is not None else asyncio.Event()
    uninstall_interrupt = install_review_interrupt(interrupt)
    provider_started = time.monotonic()
    try:
        # Open the session inside the try so a failure before or during
        # on_start() still reaches the finally that tears it down.
        if plan.use_durable_session:
            options.provider.begin_durable_session(repo_root=plan.repo_root)
            durable_session_started = True
        plan.tracker.on_start(total_chunks=len(plan.chunks), depth=options.depth)
        provider_started = time.monotonic()
        partials = await run_passes(
            context=context,
            options=options,
            plan=plan,
            progress=progress,
            interrupt=interrupt,
        )
        provider_seconds = time.monotonic() - provider_started
        plan.timings.add_phase(name=ReviewPhase.PROVIDER, seconds=provider_seconds)
        outcome = await finalize_completed_run(
            context=context,
            options=options,
            plan=plan,
            progress=progress,
            partials=partials,
            provider_seconds=provider_seconds,
            interrupt=interrupt,
        )
        completed = True
    except (AIError, ReviewExecutionError) as exc:
        # A graceful partial review: a cost cap or timeout stopped the run
        # mid-way. Detected from the raised exception, never inferred from
        # residual budget. Any other failure (auth, provider, parser) must
        # propagate so callers surface a real error via the #1101 taxonomy.
        if is_cost_cap_stop(exc=exc):
            stopped_reason = cost_cap_reason(cap=plan.budget.max_cost_usd)
        elif is_timeout_stop(exc=exc):
            stopped_reason = timeout_reason(exc=exc)
        else:
            raise
        outcome = finalize_stopped_run(
            plan=plan,
            progress=progress,
            exc=exc,
            stopped_reason=stopped_reason,
            provider_started=provider_started,
            provider_seconds=provider_seconds,
        )
        completed = True
    finally:
        # The validation span opens before cleanup so a slow durable-session
        # close or progress callback lands in a phase, not only in the total.
        validation_started = time.monotonic()
        uninstall_interrupt()
        if durable_session_started:
            options.provider.end_durable_session()
        with suppress(Exception):
            if completed:
                plan.tracker.on_complete(total_findings=outcome.total_findings)
            else:
                plan.tracker.on_abort()
    return replace(outcome, validation_started=validation_started)
