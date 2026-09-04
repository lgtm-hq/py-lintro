"""Final cross-chunk synthesis pass for ``lintro review`` (issue #2269).

Every review chunk is reviewed in isolation: its prompt carries only its own
files' diff. A bug that exists solely in the *combination* of two files split
across chunks is therefore invisible to every chunk — a signature changed in
one file with a caller updated to the wrong shape in another, a config key
renamed in one file with a consumer left reading the old name. Symmetrically,
a chunk that cannot see the other half invents phantoms about it.

This module is the one extra provider call that closes that gap: after the
chunk findings are merged, it shows the model the whole changed-file list, a
compact per-chunk digest, and as much of the whole-PR diff as its token budget
allows, and asks only for inconsistencies *between* files reviewed in
different chunks.

Off by default (``review.synthesis.enabled``) so the cost and wall-clock delta
can be measured on the #2148 timing surfaces before it is switched on.

The pass is deliberately a single seam: :func:`run_synthesis_pass` is called
from exactly one place in the orchestrator's finalize step, and it owns all of
its own prompt building, budgeting, parsing, and filtering. #1972 Phase 4 can
move that call without touching anything in here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.ai.invoke import call_ai
from lintro.ai.json_response import strip_json_fences
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.finding_origin import FindingOrigin
from lintro.ai.review.finding_matcher import fingerprint_for
from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.models.coverage_degradation import (
    SYNTHESIS_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.synthesis_outcome import SynthesisOutcome
from lintro.ai.review.sensitivity import filter_findings_by_policy
from lintro.ai.review.severity_gate import apply_cross_chunk_guard
from lintro.ai.review.synthesis_prompt import (
    build_synthesis_prompt,
    guarded_changed_paths,
    plan_synthesis_prompt,
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


#: ``findings_cap`` stamped on a synthesis coverage degradation. The synthesis
#: reasons are excluded from ``ReviewMetadata.findings_cap_applied``, so this
#: is a placeholder and never read as a per-call ceiling.
_SYNTHESIS_NO_CAP = 0


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
    """

    findings: tuple[ReviewFinding, ...] = field(default_factory=tuple)
    outcome: SynthesisOutcome = field(default_factory=SynthesisOutcome)
    degradations: tuple[CoverageDegradation, ...] = field(default_factory=tuple)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0


def should_run_synthesis(
    *,
    config: ReviewSynthesisConfig | None,
    chunks_reviewed: int,
) -> bool:
    """Decide whether the cross-chunk synthesis pass applies to this run.

    The pass exists to reason across a chunk boundary, so a run that had no
    boundary to cross gets nothing from it and must not be charged for an
    extra call.

    Args:
        config: Resolved synthesis configuration, or ``None`` when the caller
            supplied none.
        chunks_reviewed: Number of chunks that actually completed.

    Returns:
        True when the pass is enabled and the run reviewed more than one
        chunk.
    """
    if config is None or not config.enabled:
        return False
    return chunks_reviewed > 1


def _parse_synthesis_findings(*, content: str) -> tuple[ReviewFinding, ...] | None:
    """Parse the pass's response with the shared finding parser.

    Args:
        content: Raw model response text.

    Returns:
        Parsed findings, or ``None`` when the response was not a JSON object
        carrying a ``findings`` list — a missing key counts the same as a
        malformed value. The caller records that as a failed pass rather than
        an empty one, so "found nothing" and "could not be read" never look
        alike. Only a present, empty list is an empty success.
    """
    try:
        payload = json.loads(strip_json_fences(content=content))
    except (json.JSONDecodeError, ValueError):
        logger.warning("Could not parse the cross-chunk synthesis response as JSON.")
        return None
    if not isinstance(payload, dict):
        logger.warning("The cross-chunk synthesis payload was not an object.")
        return None
    if "findings" not in payload:
        # An answer that never mentions ``findings`` did not answer. Defaulting
        # it to an empty list would report "found no cross-file
        # inconsistencies" for a call that produced nothing usable.
        logger.warning("The cross-chunk synthesis payload had no findings key.")
        return None
    raw = payload["findings"]
    if not isinstance(raw, list):
        # ``parse_findings`` would quietly render a string, a mapping, or a
        # null here as no findings at all, which is exactly the "empty
        # success" this pass promises never to confuse with a failure.
        logger.warning("The cross-chunk synthesis findings value was not a list.")
        return None
    return parse_findings(raw_findings=raw)


def _deduplicate(
    *,
    candidates: Sequence[ReviewFinding],
    existing: Sequence[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Drop synthesized findings the chunk passes already reported.

    Identity is the cross-round fingerprint the state ledger already uses, so
    a synthesized restatement collapses onto the chunk finding it duplicates
    instead of appearing beside it under a slightly different line number.

    Args:
        candidates: Synthesized findings, in reported order.
        existing: Findings already merged from the chunk passes.

    Returns:
        The candidates whose fingerprint is new, in reported order.
    """
    seen = {
        fingerprint_for(
            file=finding.file,
            category=finding.category,
            title=finding.title,
        )
        for finding in existing
    }
    kept: list[ReviewFinding] = []
    for finding in candidates:
        fingerprint = fingerprint_for(
            file=finding.file,
            category=finding.category,
            title=finding.title,
        )
        if fingerprint in seen:
            logger.debug(
                "Dropping synthesized finding {title!r}: already reported.",
                title=finding.title,
            )
            continue
        seen.add(fingerprint)
        kept.append(finding)
    return tuple(kept)


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
            findings_cap=_SYNTHESIS_NO_CAP,
        ),
    ]
    if truncated:
        degradations.insert(
            0,
            CoverageDegradation(
                reason=CoverageDegradationReason.SYNTHESIS_TRUNCATED,
                chunk_index=SYNTHESIS_CHUNK_INDEX,
                findings_cap=_SYNTHESIS_NO_CAP,
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


async def run_synthesis_pass(
    *,
    context: ReviewContext,
    summaries: Sequence[ChunkSummary],
    existing_findings: Sequence[ReviewFinding],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    config: ReviewSynthesisConfig,
    policy: ReviewSensitivityPolicy,
    budget: CostBudget,
    repo_root: str = "",
    use_one_shot: bool = True,
    diff_budget: int = 1,
    stop: asyncio.Event | None = None,
) -> SynthesisPass:
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

    Returns:
        The pass result. Any failure — a budget stop, a provider error, an
        unreadable response — comes back as a failed pass with a coverage
        degradation, never as an exception: the chunk findings stand and the
        run stays complete for them.
    """
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

    parsed = _parse_synthesis_findings(content=response.content)
    if parsed is None:
        return _failed_pass(
            truncated=truncated,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_estimate=response.cost_estimate,
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
    deduplicated = _deduplicate(candidates=guarded, existing=existing_findings)
    kept = deduplicated[: max(config.max_findings, 1)]

    degradations = (
        (
            CoverageDegradation(
                reason=CoverageDegradationReason.SYNTHESIS_TRUNCATED,
                chunk_index=SYNTHESIS_CHUNK_INDEX,
                findings_cap=_SYNTHESIS_NO_CAP,
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
        ),
        degradations=degradations,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_estimate=response.cost_estimate,
    )
