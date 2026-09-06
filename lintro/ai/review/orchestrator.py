"""The review run: plan it, execute it, report it (issue #2301).

``run_review`` is the public facade and the sync/async boundary; everything
below it is async. ``run_review_async`` is three steps and no more:

1. :func:`~lintro.ai.review.run_planning.plan_run` resolves the sensitivity
   policy, the diff budget, the chunks, the resume plan, the custom-agent
   selection and the concurrency ceiling into one
   :class:`~lintro.ai.review.run_planning.ReviewRunPlan`.
2. :func:`~lintro.ai.review.run_execution.execute_run` makes the provider
   calls — the chunk fan-out in :mod:`lintro.ai.review.chunk_runner`, the
   custom-agent passes, the merge, and the optional cross-chunk synthesis
   pass — and finalizes whether the run completed or stopped gracefully on a
   cost cap, timeout or SIGTERM.
3. :func:`~lintro.ai.review.result_assembly.assemble_review_result` turns the
   plan and the outcome into the :class:`ReviewResult` every surface renders.

Prompts, merge policy, per-chunk passes and result assembly all live in their
own modules; this one owns the sequence and nothing else. See
``docs/architecture/AI-REVIEW-EXECUTION.md``.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from lintro.ai.review.result_assembly import (
    assemble_review_result,
    empty_review_result,
)
from lintro.ai.review.run_execution import execute_run
from lintro.ai.review.run_planning import plan_run
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.synthesis_prompt import guarded_changed_paths
from lintro.ai.review.timings import ReviewPhase, ReviewTimingRecorder

if TYPE_CHECKING:
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_result import ReviewResult

__all__ = [
    "guard_changed_paths",
    "run_review",
    "run_review_async",
]


def run_review(
    context: ReviewContext,
    *,
    options: ReviewSessionOptions,
) -> ReviewResult:
    """Execute an AI diff review from synchronous code.

    This is the sync/async boundary for ``lintro review``: the review
    pipeline below it is async, and ``asyncio.run`` is entered exactly
    once here so one event loop (and one provider client) serves the
    whole review.

    Every setting a run takes lives on
    :class:`~lintro.ai.review.session.ReviewSessionOptions` — including the
    defaults, which are declared there once rather than on this facade as
    well (#2301). Callers build the object and this function forwards it.

    Args:
        context: Collected review diff context.
        options: Session options for the run — provider, AI config, depth,
            checklist, sensitivity, resume state, and stop event.

    Returns:
        Complete review result with metadata, checklist, and findings.
    """
    return asyncio.run(run_review_async(context, options=options))


def guard_changed_paths(*, context: ReviewContext) -> tuple[str, ...]:
    """Return every path the cross-chunk guard treats as changed by the PR.

    One implementation, re-exported. It lives in
    :mod:`lintro.ai.review.synthesis_prompt` because the synthesis pass needs
    the same list and the dependency only runs one way — this module imports
    the pass, which imports that module — so the reverse import would close a
    cycle.

    Args:
        context: Collected review context.

    Returns:
        Changed paths and rename/copy sources, in changed-file order.
    """
    return guarded_changed_paths(context=context)


async def run_review_async(
    context: ReviewContext,
    *,
    options: ReviewSessionOptions,
) -> ReviewResult:
    """Execute an AI diff review with depth-controlled passes.

    Three steps, one per collaborator:
    :func:`~lintro.ai.review.run_planning.plan_run` resolves what the run will
    do, :func:`~lintro.ai.review.run_execution.execute_run` makes the provider
    calls, and
    :func:`~lintro.ai.review.result_assembly.assemble_review_result` turns the
    two into the reported result.

    A non-recoverable failure (``AIError`` for provider authentication or a
    genuine provider error, ``ReviewExecutionError`` for a chunk that failed
    mid-run) propagates out of
    :func:`~lintro.ai.review.run_execution.execute_run`. A cost-cap, timeout or
    SIGTERM stop does not: it is handled there and returned as a partial result.

    Args:
        context: Collected review diff context.
        options: Session options for the run — provider, AI config, depth,
            checklist, sensitivity, resume state, and stop event. See
            :class:`~lintro.ai.review.session.ReviewSessionOptions`.

    Returns:
        Complete review result with metadata, checklist, and findings.

    Raises:
        ValueError: If ``options.depth`` is outside the allowed range 1-3.
    """
    if options.depth < 1 or options.depth > 3:
        raise ValueError(f"depth must be between 1 and 3, got {options.depth}")

    if not context.changed_files and not context.unified_diff.strip():
        return empty_review_result(context=context, options=options)

    # One monotonic clock for the whole run: the recorder is back-dated by the
    # context-collection time already spent so ``total_seconds`` (and the
    # reported duration) covers the whole wait, not just the phases after the
    # early-return (#2148).
    timings = ReviewTimingRecorder(
        started_at=time.monotonic() - max(options.context_collection_seconds, 0.0),
    )
    timings.add_phase(
        name=ReviewPhase.CONTEXT_COLLECTION,
        seconds=options.context_collection_seconds,
    )

    plan = plan_run(context=context, options=options, timings=timings)
    outcome = await execute_run(context=context, options=options, plan=plan)
    return assemble_review_result(
        context=context,
        options=options,
        plan=plan,
        outcome=outcome,
    )
