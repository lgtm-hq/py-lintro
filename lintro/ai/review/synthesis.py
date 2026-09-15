"""The round's synthesis pass for ``lintro review`` (#2269, lintro-ops #37).

Every review chunk reports findings only and sees only its own files' diff.
This module is the one extra provider call per round that closes both gaps:
after the chunk findings are merged, it shows the model the whole
changed-file list, a digest of every reported finding, and as much of the
whole-PR diff as its token budget allows, and asks for the round's summary
and verdict reasoning, for duplicate findings that share a root cause, and
for inconsistencies *between* files reviewed in different chunks.

On by default (``review.synthesis.enabled``). The pass is deliberately a
single seam: :func:`run_synthesis_pass` is called from exactly one place in
the orchestrator's finalize step, and it owns all of its own prompt building,
budgeting, parsing, and filtering. A failure never ends the run: the chunk
findings stand, the narrative is absent, and ``synthesis_failed`` is recorded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.ai.cli_schemas import cli_schema_for_synthesis
from lintro.ai.invoke import call_ai
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.finding_origin import FindingOrigin
from lintro.ai.review.models.coverage_degradation import (
    SYNTHESIS_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_summary import ReviewSummary
from lintro.ai.review.models.synthesis_outcome import SynthesisOutcome
from lintro.ai.review.models.verdict_reasoning import VerdictReasoning
from lintro.ai.review.sensitivity import filter_findings_by_policy
from lintro.ai.review.severity_gate import apply_cross_chunk_guard
from lintro.ai.review.synthesis_narrative import (
    apply_duplicate_groups,
    parse_synthesis_envelope,
)
from lintro.ai.review.synthesis_prompt import (
    build_synthesis_prompt,
    guarded_changed_paths,
    plan_synthesis_prompt,
)
from lintro.ai.review.synthesis_response import (
    deduplicate_synthesis_findings,
    parse_synthesis_findings,
)

if TYPE_CHECKING:
    from lintro.ai.budget import CostBudget
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.providers.response import AIResponse
    from lintro.ai.review.models.chunk_summary import ChunkSummary
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.sensitivity import ReviewSensitivityPolicy
    from lintro.config.review_config import ReviewSynthesisConfig

__all__ = [
    "SYNTHESIS_CHUNK_INDEX",
    "SynthesisPass",
    "SynthesisPassRequest",
    "run_synthesis_pass",
    "should_run_synthesis",
]


class _SynthesisInterruptedError(Exception):
    """The run was interrupted while the extra call was still in flight.

    Raised only inside this module and caught by the pass's own fail-soft
    handler, which turns it into a failed pass. It is an ordinary
    ``Exception`` rather than a ``CancelledError`` so that handler — which
    deliberately catches ``Exception`` and not ``BaseException`` — sees it.
    """


@dataclass(frozen=True, slots=True)
class SynthesisPass:
    """Everything one synthesis pass contributed to a run.

    Attributes:
        findings: Synthesized findings that survived the P1 evidence gate,
            the sensitivity policy, the cross-chunk contradiction guard, the
            cap, and deduplication. Each carries
            ``origin=FindingOrigin.SYNTHESIS``; one the guard tagged also
            carries ``cross_chunk_contradiction``.
        outcome: What the pass did, for the JSON payload and the shared note.
        degradations: Coverage degradations the pass incurred — a truncated
            input, a failed call, or both. Never empty when the pass could not
            reason over the whole PR.
        input_tokens: Prompt tokens the extra call consumed.
        output_tokens: Completion tokens the extra call produced.
        cost_estimate: Estimated USD cost of the extra call.
        summary: The round's headline and walkthrough, or ``None`` when the
            pass failed or wrote none.
        verdict_reasoning: The round's verdict explanation, or ``None``.
        merged_findings: The chunk findings after the pass's duplicate merges
            were applied, or ``None`` when the pass failed (the caller keeps
            the unmerged set).
    """

    findings: tuple[ReviewFinding, ...] = field(default_factory=tuple)
    outcome: SynthesisOutcome = field(default_factory=SynthesisOutcome)
    degradations: tuple[CoverageDegradation, ...] = field(default_factory=tuple)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0
    summary: ReviewSummary | None = None
    verdict_reasoning: VerdictReasoning | None = None
    merged_findings: tuple[ReviewFinding, ...] | None = None


def should_run_synthesis(
    *,
    config: ReviewSynthesisConfig | None,
    chunks_reviewed: int,
) -> bool:
    """Decide whether the synthesis pass applies to this run.

    The pass writes the round's summary and verdict reasoning, so it applies
    to every run that reviewed at least one chunk — a single-chunk PR needs
    its narrative as much as a ten-chunk one (lintro-ops milestone 0).

    Args:
        config: Resolved synthesis configuration, or ``None`` when the caller
            supplied none.
        chunks_reviewed: Number of chunks that actually completed.

    Returns:
        True when the pass is enabled and at least one chunk was reviewed.
    """
    if config is None or not config.enabled:
        return False
    return chunks_reviewed >= 1


def _failed_pass(
    *,
    truncated: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_estimate: float = 0.0,
) -> SynthesisPass:
    """Build the result for a pass that ran but produced nothing usable.

    Args:
        truncated: Whether the input had already been truncated.
        input_tokens: Prompt tokens spent before the failure, if any.
        output_tokens: Completion tokens produced before the failure, if any.
        cost_estimate: Estimated USD cost incurred before the failure.

    Returns:
        A pass carrying no findings, a failed outcome, and the degradations
        the run must report. Never raises: a synthesis failure degrades the
        run, it does not end it.
    """
    degradations = [
        CoverageDegradation(
            reason=CoverageDegradationReason.SYNTHESIS_FAILED,
            chunk_index=SYNTHESIS_CHUNK_INDEX,
        ),
    ]
    if truncated:
        degradations.insert(
            0,
            CoverageDegradation(
                reason=CoverageDegradationReason.SYNTHESIS_TRUNCATED,
                chunk_index=SYNTHESIS_CHUNK_INDEX,
            ),
        )
    return SynthesisPass(
        outcome=SynthesisOutcome(findings_added=0, truncated=truncated, failed=True),
        degradations=tuple(degradations),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=cost_estimate,
    )


async def _await_call_until_stop(
    *,
    call: Coroutine[Any, Any, AIResponse],
    stop: asyncio.Event | None,
) -> AIResponse:
    """Await the pass's one provider call, abandoning it on an interrupt.

    The same ``asyncio.wait`` race the chunk fan-out uses for SIGTERM. It
    matters here because the pass runs after every chunk has been reviewed:
    the run has real coverage to persist, and a bare await would hold the
    process in the provider call for the whole shutdown window instead.

    Args:
        call: The pending provider call.
        stop: Event set by the run's SIGTERM/SIGINT handler, or ``None`` when
            the caller registered no interrupt.

    Returns:
        The provider response.

    Raises:
        _SynthesisInterruptedError: When the stop event won the race. The pass's
            fail-soft handler turns that into a failed pass, so the chunk
            findings and the resume checkpoint still stand.
    """
    if stop is None:
        return await call
    call_task = asyncio.ensure_future(call)
    stop_task = asyncio.ensure_future(stop.wait())
    try:
        done, _pending = await asyncio.wait(
            {call_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done and stop.is_set() and not call_task.done():
            raise _SynthesisInterruptedError
        return await call_task
    finally:
        for task in (call_task, stop_task):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


@dataclass(frozen=True, slots=True, kw_only=True)
class SynthesisPassRequest:
    """Everything the cross-chunk synthesis pass reads.

    Grouping the pass's inputs keeps :func:`run_synthesis_pass` to one
    argument and makes a new input a field here rather than another keyword
    threaded through the caller (issue #2301).

    Attributes:
        context: Collected review diff context.
        summaries: Per-chunk digests in chunk order.
        existing_findings: Findings already merged from the chunk passes,
            deduplicated against.
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget, and timeouts.
        config: Resolved synthesis configuration.
        policy: Run sensitivity policy applied to the synthesized findings.
        budget: Session cost budget tracker.
        repo_root: Absolute path to the repository under review.
        use_one_shot: When True, avoid durable provider sessions.
        diff_budget: Token budget the whole prompt must fit — the digest,
            the changed-file list, and the diff together.
        stop: Event set by the run's interrupt handler. When it fires while
            the extra call is in flight the call is abandoned and the pass
            reports a failure, so a SIGTERM cannot hold the process in an
            optional call while there is a completed review to persist.
    """

    context: ReviewContext
    summaries: Sequence[ChunkSummary]
    existing_findings: Sequence[ReviewFinding]
    provider: BaseAIProvider
    ai_config: AIConfig
    config: ReviewSynthesisConfig
    policy: ReviewSensitivityPolicy
    budget: CostBudget
    repo_root: str = ""
    use_one_shot: bool = True
    diff_budget: int = 1
    stop: asyncio.Event | None = None


async def run_synthesis_pass(*, request: SynthesisPassRequest) -> SynthesisPass:
    """Run the whole-PR cross-chunk pass and return what it contributed.

    Exactly one provider call, on the same ``call_ai`` transport and behind
    the same redaction as every chunk call. Its findings then pass every
    filter a chunk finding passes, in this order:

    - the **P1 evidence gate**, applied by the shared finding parser: a
      phantom P1 with no failure mechanism comes back as a marked,
      non-blocking P2 rather than failing the review;
    - the run's **sensitivity policy**, so a preset that drops a band drops
      it here too;
    - the **cross-chunk contradiction guard** (#2265), so a phantom that does
      name a failure mechanism but claims a file the PR changed was never
      touched is tagged ``cross_chunk_contradiction`` and moved down one band
      — the pass sees the whole PR, so a claim like that is wrong here for
      the same reason it is wrong in a chunk;
    - deduplication against the chunk findings, then the configured
      ``max_findings`` cap. Both run on the guarded severity, so a tagged
      finding cannot survive a dedupe drop under a different fingerprint, and
      dedupe running first means a restatement can never consume a slot in
      the cap window that a novel cross-file finding needed.

    A finding that survives all of that can still be discarded downstream:
    ``reject_context_findings`` drops a finding on a path the round was not
    asked to re-review, which is why the orchestrator recounts
    ``findings_added`` from the surviving findings rather than trusting the
    tally this function returns.

    Args:
        request: The pass's inputs.

    Returns:
        The pass result. Any failure — a budget stop, a provider error, an
        unreadable response — comes back as a failed pass with a coverage
        degradation, never as an exception: the chunk findings stand and the
        run stays complete for them.
    """
    context = request.context
    summaries = request.summaries
    existing_findings = request.existing_findings
    provider = request.provider
    ai_config = request.ai_config
    config = request.config
    policy = request.policy
    budget = request.budget
    repo_root = request.repo_root
    use_one_shot = request.use_one_shot
    diff_budget = request.diff_budget
    stop = request.stop

    plan = plan_synthesis_prompt(
        context=context,
        summaries=summaries,
        diff_budget=diff_budget,
    )
    truncated = plan.truncated
    system_prompt, user_prompt = build_synthesis_prompt(
        context=context,
        plan=plan,
        max_findings=config.max_findings,
    )
    try:
        budget.check()
        response = await _await_call_until_stop(
            call=call_ai(
                provider=provider,
                ai_config=ai_config,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                budget=budget,
                repo_root=repo_root or None,
                use_one_shot=use_one_shot,
                cli_schema=cli_schema_for_synthesis(transport=ai_config.transport),
            ),
            stop=stop,
        )
    except _SynthesisInterruptedError:
        logger.warning(
            "The cross-chunk synthesis pass was interrupted; keeping the "
            "chunk findings and marking coverage degraded.",
        )
        return _failed_pass(truncated=truncated)
    except Exception:
        # Deliberately broad: this pass is additive, so nothing it can raise —
        # a cost-cap stop, a provider error, a timeout — may be allowed to
        # turn a completed review into a failed or partial one.
        #
        # A cost-cap stop *during this call* is therefore recorded as
        # SYNTHESIS_FAILED and nothing else: the run stays complete and
        # non-partial, and ``stopped_reason`` stays empty. That is deliberate
        # and not a lost budget signal. ``partial`` means planned review work
        # was left undone, and by the time this call is made every chunk has
        # already been reviewed — the only thing the cap cost the run is the
        # optional cross-file sweep, which is exactly what the degradation
        # says. Re-raising here would downgrade a finished review to a partial
        # one over an extra call it was never required to make.
        logger.opt(exception=True).warning(
            "The cross-chunk synthesis pass failed; keeping the chunk "
            "findings and marking coverage degraded.",
        )
        return _failed_pass(truncated=truncated)

    parsed = parse_synthesis_findings(content=response.content)
    if parsed is None:
        return _failed_pass(
            truncated=truncated,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_estimate=response.cost_estimate,
        )
    narrative = parse_synthesis_envelope(content=response.content)
    merged_findings, duplicates_merged = apply_duplicate_groups(
        findings=existing_findings,
        groups=narrative.duplicates,
    )

    tagged = tuple(
        replace(finding, origin=FindingOrigin.SYNTHESIS) for finding in parsed
    )
    gated = filter_findings_by_policy(findings=tagged, policy=policy)
    # #2265 applies to this pass too. Guarding here, rather than leaning on
    # the orchestrator's finalize guard alone, keeps the pass self-contained
    # and puts the tag on before the cap and the dedupe: whatever this
    # returns is already guarded, so a tagged phantom can never survive a
    # dedupe drop under a different fingerprint. The guard is idempotent, so
    # the finalize pass over the merged list leaves these findings alone.
    guarded = apply_cross_chunk_guard(
        findings=gated,
        changed_paths=guarded_changed_paths(context=context),
    )
    # Dedupe first, then cap. A restatement of a chunk finding contributes
    # nothing, so letting one consume a slot in the cap window would discard a
    # novel cross-file finding that came after it — the exact thing this pass
    # exists to surface.
    deduplicated = deduplicate_synthesis_findings(
        candidates=guarded,
        existing=merged_findings,
    )
    kept = deduplicated[: max(config.max_findings, 1)]

    degradations = (
        (
            CoverageDegradation(
                reason=CoverageDegradationReason.SYNTHESIS_TRUNCATED,
                chunk_index=SYNTHESIS_CHUNK_INDEX,
            ),
        )
        if truncated
        else ()
    )
    logger.info(
        "Cross-chunk synthesis reported {n} finding(s) after cap and dedupe.",
        n=len(kept),
    )
    return SynthesisPass(
        findings=kept,
        outcome=SynthesisOutcome(
            findings_added=len(kept),
            truncated=truncated,
            failed=False,
            duplicates_merged=duplicates_merged,
        ),
        degradations=degradations,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_estimate=response.cost_estimate,
        summary=narrative.summary,
        verdict_reasoning=narrative.verdict_reasoning,
        merged_findings=merged_findings,
    )
