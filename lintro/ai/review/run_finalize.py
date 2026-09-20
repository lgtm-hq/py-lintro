"""Finalizing a completed run: synthesis, verification, then the gates (#2728).

Split out of :mod:`lintro.ai.review.run_execution` when the verification pass
landed. The order here is the contract: the synthesis pass adds and merges
findings, the verification pass may drop or lower them, and the mechanical
severity gates run last so they read the verified severities. The built-in
passes parse with ``gate_severity=False``; this is the one place the round's
findings are gated.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.ai.review.merge import finalize_partials
from lintro.ai.review.repo_context import repo_context_source_for
from lintro.ai.review.result_assembly import ReviewRunOutcome
from lintro.ai.review.severity_gate import apply_severity_gates
from lintro.ai.review.synthesis import (
    SynthesisPassRequest,
    run_synthesis_pass,
    should_run_synthesis,
)
from lintro.ai.review.synthesis_prompt import chunk_summaries
from lintro.ai.review.timings import ReviewPhase
from lintro.ai.review.verification import (
    VerificationPassRequest,
    run_verification_pass,
)

if TYPE_CHECKING:
    from lintro.ai.review.merge import ChunkReviewPartial
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.run_execution import RunProgress
    from lintro.ai.review.run_planning import ReviewRunPlan
    from lintro.ai.review.session import ReviewSessionOptions

__all__ = ["finalize_completed_run", "merge_partials"]


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
        questions=progress.questions,
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
        return await _verify_and_gate(
            context=context,
            options=options,
            plan=plan,
            outcome=outcome,
            interrupt=interrupt,
        )
    # ``should_run_synthesis`` already rejected a None config; bind for mypy.
    synthesis_config = options.synthesis
    assert synthesis_config is not None
    with plan.timings.phase(name=ReviewPhase.SYNTHESIS):
        synthesis_pass = await run_synthesis_pass(
            request=SynthesisPassRequest(
                context=context,
                summaries=chunk_summaries(chunks=plan.chunks, partials=partials),
                existing_findings=outcome.filtered_findings,
                provider=options.provider,
                ai_config=plan.ai_config,
                config=synthesis_config,
                policy=plan.policy,
                budget=plan.budget,
                repo_root=plan.repo_root,
                # Never reuse the built-in review's durable session: the pass
                # is a standalone whole-PR question, not a chunk.
                use_one_shot=True,
                diff_budget=plan.synthesis_diff_budget,
                # The chunk fan-out already raced this event so a SIGTERM can
                # persist coverage inside the runner's shutdown window; the
                # extra call gets the same treatment, and a stop that lands
                # here is recorded as a failed pass.
                stop=interrupt,
            ),
        )
    # A successful pass returns the chunk findings with duplicates merged.
    base_findings = (
        synthesis_pass.merged_findings
        if synthesis_pass.merged_findings is not None
        else outcome.filtered_findings
    )
    findings = base_findings + synthesis_pass.findings
    return await _verify_and_gate(
        context=context,
        options=options,
        plan=plan,
        outcome=replace(
            outcome,
            synthesis_pass=synthesis_pass,
            filtered_findings=findings,
            total_findings=len(findings),
        ),
        interrupt=interrupt,
    )


async def _verify_and_gate(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    plan: ReviewRunPlan,
    outcome: ReviewRunOutcome,
    interrupt: asyncio.Event,
) -> ReviewRunOutcome:
    """Run the verification pass, then the severity gates, on a completed run.

    The order is the #2728 contract: the verifier sees the model's own
    severities and may drop or lower them, and the mechanical P1 and P2
    gates run after it so they read the verified severities. The built-in
    passes therefore parse with ``gate_severity=False`` and this is the one
    place the round's findings are gated.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        plan: The resolved run plan.
        outcome: The outcome after synthesis, with the round's findings.
        interrupt: Event a SIGTERM/SIGINT handler sets to stop the run.

    Returns:
        The outcome with verified, gated findings and the pass summary.
    """
    # Custom-agent findings carry an author-declared severity policy, which
    # is configuration, not model output: neither the verifier nor the gates
    # touch them, so they are set aside and re-appended unchanged.
    custom = set(map(id, outcome.custom_findings))
    builtin = tuple(f for f in outcome.filtered_findings if id(f) not in custom)
    with plan.timings.phase(name=ReviewPhase.VERIFICATION):
        verification = await run_verification_pass(
            request=VerificationPassRequest(
                context=context,
                findings=builtin,
                mode=options.verify,
                provider=options.provider,
                ai_config=plan.ai_config,
                budget=plan.budget,
                repo_source=repo_context_source_for(
                    context=context,
                    ai_config=plan.ai_config,
                ),
                repo_root=plan.repo_root,
                # A standalone question over the round, not a chunk.
                use_one_shot=True,
                stop=interrupt,
            ),
        )
    findings = apply_severity_gates(findings=verification.findings)
    findings = findings + tuple(outcome.custom_findings)
    return replace(
        outcome,
        verification=verification.summary,
        filtered_findings=findings,
        total_findings=len(findings),
    )
