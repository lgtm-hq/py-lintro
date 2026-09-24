"""Provider calls for one review run, and how the run is finalized (#2301).

Split out of :mod:`lintro.ai.review.orchestrator` (the public facade): chunk
fan-out, custom-agent passes, merge, optional synthesis, and the run finalizers.

Every function was moved verbatim (behaviour, timings and graceful-stop
handling unchanged); see ``docs/architecture/AI-REVIEW-EXECUTION.md``.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.exceptions import AIError
from lintro.ai.review.chunk_runner import review_all_chunks
from lintro.ai.review.custom_agent_runner import (
    CustomAgentPassRequest,
    run_custom_agent_passes,
)
from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.incremental_coverage import checkpoint_writer
from lintro.ai.review.interrupt import install_review_interrupt
from lintro.ai.review.pr_budget import cost_stop_reason, round_cap
from lintro.ai.review.question_pass import RunQuestions, run_question_pass
from lintro.ai.review.repo_context import repo_context_source_for
from lintro.ai.review.result_assembly import (
    ReviewRunOutcome,
)
from lintro.ai.review.run_finalize import (
    finalize_completed_run,
    gate_built_in_findings,
    merge_partials,
)
from lintro.ai.review.session import (
    ChunkRunPlan,
    is_cost_cap_stop,
    is_timeout_stop,
    stop_hint,
    timeout_reason,
)
from lintro.ai.review.timings import ReviewPhase

if TYPE_CHECKING:
    from lintro.ai.review.custom_agent_runner import CustomAgentPassResult
    from lintro.ai.review.merge import ChunkReviewPartial
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.run_planning import ReviewRunPlan
    from lintro.ai.review.session import ReviewSession, ReviewSessionOptions

__all__ = [
    "RunProgress",
    "chunk_run_plan",
    "execute_run",
    "finalize_stopped_run",
    "run_passes",
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
        tools_disabled=plan.tools_disabled,
        strictness_section=plan.strictness_section,
        diff_budget=plan.diff_budget,
        max_parallel_calls=plan.max_parallel_calls,
        stop=interrupt,
        timings=plan.timings,
        repo_context=repo_context_source_for(context=context, ai_config=plan.ai_config),
        context_budget=plan.context_budget,
        diff_ceiling=plan.diff_ceiling,
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
        questions: The once-per-run question pass result (#2720), when run.
    """

    collected: list[ChunkReviewPartial] = field(default_factory=list)
    custom_results: list[CustomAgentPassResult] = field(default_factory=list)
    custom_agents_failed: list[str] = field(default_factory=list)
    questions: RunQuestions | None = None


async def run_passes(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    plan: ReviewRunPlan,
    progress: RunProgress,
    interrupt: asyncio.Event,
    session: ReviewSession,
) -> list[ChunkReviewPartial]:
    """Run the chunk fan-out and then the scoped custom-agent passes.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        plan: The resolved run plan.
        progress: Accumulator the completed work is appended to.
        interrupt: Event a SIGTERM/SIGINT handler sets to stop the run.
        session: The run's provider owner (#2302).

    Returns:
        The completed chunk partials, in chunk order.
    """
    partials: list[ChunkReviewPartial] = []
    if plan.chunks:
        questions = await run_question_pass(
            context=context,
            options=options,
            plan=plan,
            stop=interrupt,
        )
        # Recorded before the fan-out so a run the fan-out stops (cost cap,
        # timeout, SIGTERM) still reports the pass it already paid for; the
        # result assembly charges its usage and degradation from here.
        progress.questions = questions
        chunk_plan = replace(
            chunk_run_plan(
                context=context,
                options=options,
                plan=plan,
                interrupt=interrupt,
            ),
            generated_questions=questions.text,
        )
        partials = await review_all_chunks(
            chunks=plan.chunks,
            plan=chunk_plan,
            completed_sink=progress.collected,
            on_chunk_complete=checkpoint_writer(
                resume=plan.resume,
                context=context,
                prior_state=options.prior_state,
                force_full=options.force_full,
                policy=plan.policy,
                round_spend=lambda: plan.budget.spent,
            ),
        )
    if plan.resume.queue:
        await run_custom_agent_passes(
            request=CustomAgentPassRequest(
                selected=plan.agent_selection.selected,
                context=context,
                provider=options.provider,
                ai_config=plan.ai_config,
                budget=plan.budget,
                repo_root=plan.repo_root,
                workspace_root=options.workspace_root,
                # Never reuse the built-in review's durable session: each
                # agent is an independent, narrowly scoped pass with its own
                # instructions.
                use_one_shot=True,
                tools_disabled=plan.tools_disabled,
                on_pass_complete=progress.custom_results.append,
                on_agent_failed=progress.custom_agents_failed.append,
                # Model-override providers land in the session's cache, so
                # they close with the run rather than leaking (#2302).
                provider_cache=session.provider_cache,
            ),
        )
    return partials


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
    # No verification on a stopped run (the round is not complete), but the
    # gates still apply: the chunk pass parses ungated since #2728.
    outcome = gate_built_in_findings(outcome=outcome)
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
    session: ReviewSession,
) -> ReviewRunOutcome:
    """Make the run's provider calls and finalize however it ends.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        plan: The resolved run plan.
        session: The run's provider owner, forwarded so a custom agent's
            ``model`` override registers its provider with the run (#2302).

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
            session=session,
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
            stopped_reason = cost_stop_reason(
                round_cap=round_cap(options=options),
                pr_budget=options.pr_budget,
            )
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
