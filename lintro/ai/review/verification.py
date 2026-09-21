"""The verification pass: one refutation call per round (#2728, step 0.11).

The pipeline has a recall mechanism (the adversarial sweep asks "what did I
miss?") but, before this, nothing that asked "is this finding real?" — the
verdict's precision rested on prompt calibration plus the mechanical gates.
This pass puts the findings that decide the verdict to the model once more,
prompted to *refute*: every P1 and, by default, every finding the reviewer
marked low-confidence, each with its cited hunk and the surrounding
post-change code inside the 0.9 boundary fencing. One provider call per
round covers all of them (ADR-0010 decision A), on the run's own model — the
escalation-model knob is the cascade issue's, not this step's.

Three outcomes per finding: ``confirmed`` (kept, marked verified),
``refuted`` (dropped, recorded on the run and in the transcript log),
``downgraded`` (a P1 whose failure scenario did not hold becomes P2 with
:attr:`~lintro.ai.review.enums.severity_downgrade_reason.SeverityDowngradeReason.REFUTATION_WEAKENED`).
The pass runs after synthesis and *before* the mechanical severity gates, so
the gates see its severities. It is fail-soft: any failure keeps the selected
findings unverified and records ``VERIFICATION_FAILED``.

Every provider call below goes through :mod:`lintro.ai.review.provider_call`,
the single seam tests replace.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.ai.cli_schemas import cli_schema_for_verification
from lintro.ai.exceptions import AITurnLimitError
from lintro.ai.prompts.review import (
    REVIEW_VERIFICATION_SYSTEM_PROMPT,
    REVIEW_VERIFICATION_USER_PROMPT_TEMPLATE,
)
from lintro.ai.review import provider_call
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.models.coverage_degradation import (
    SYNTHESIS_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.verification_outcome import (
    VerificationSummary,
)
from lintro.ai.review.prompt_redaction import redact_prompt_text
from lintro.ai.review.verification_prompt import render_verification_findings
from lintro.ai.review.verification_response import (
    apply_verification_verdicts,
    parse_verification_answer,
)
from lintro.ai.sanitize import make_boundary_marker
from lintro.ai.token_budget import estimate_tokens
from lintro.config.review_config import ReviewVerifyMode

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

    from lintro.ai.budget import CostBudget
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.providers.response import AIResponse
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.repo_context import RepoContextSource

__all__ = [
    "MAX_VERIFICATION_FINDINGS",
    "VerificationPass",
    "VerificationPassRequest",
    "run_verification_pass",
    "select_for_verification",
    "verification_degradations",
]

#: Upper bound on findings put to the verifier in one call. Past it the
#: lowest-priority selections (low-confidence P3s first) are left unverified
#: rather than making the call unanswerable.
MAX_VERIFICATION_FINDINGS = 12

#: Completion budget for the answer: a few lines per finding.
_MAX_TOKENS = 4_096

_LOW_CONFIDENCE = "low"


@dataclass(frozen=True, slots=True, kw_only=True)
class VerificationPassRequest:
    """Everything the verification pass reads.

    Attributes:
        context: Collected review diff context (PR title for the prompt).
        findings: The round's findings after synthesis, before the gates.
        mode: Which findings to verify.
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget and timeouts.
        budget: Session cost budget tracker.
        repo_source: Head-side reader for the cited code; ``None`` sends the
            findings without surrounding code.
        repo_root: Absolute path to the repository under review.
        allowed_paths: Paths whose head content may be shown to the
            verifier; a finding on any other path is sent without its
            cited code. ``None`` allows the changed files in ``context``.
        use_one_shot: When True, avoid durable provider sessions.
        stop: Event set by the run's interrupt handler; when it fires while
            the call is in flight the call is abandoned and the pass fails
            soft.
    """

    context: ReviewContext
    findings: Sequence[ReviewFinding]
    mode: ReviewVerifyMode
    provider: BaseAIProvider
    ai_config: AIConfig
    budget: CostBudget
    repo_source: RepoContextSource | None = None
    repo_root: str = ""
    allowed_paths: frozenset[str] | None = None
    use_one_shot: bool = True
    stop: asyncio.Event | None = None


@dataclass(frozen=True, slots=True)
class VerificationPass:
    """What one verification pass did to the round's findings.

    Attributes:
        findings: The round's findings after the pass: refuted ones removed,
            confirmed ones marked ``verified``, downgraded ones rewritten.
        summary: Counts, refutations and usage for the surfaces and record.
    """

    findings: tuple[ReviewFinding, ...]
    summary: VerificationSummary


class _VerificationInterruptedError(Exception):
    """The run's stop event won the race against the provider call."""


def select_for_verification(
    *,
    findings: Sequence[ReviewFinding],
    mode: ReviewVerifyMode,
) -> tuple[int, ...]:
    """Return the indices of the findings the pass will verify.

    Questions are never verified: they carry no severity and never move the
    verdict. Selection order is P1s first (they decide "Blocked"), then
    low-confidence findings by severity, so a cap cuts the least
    consequential first.

    Args:
        findings: The round's findings, in order.
        mode: Which findings to verify.

    Returns:
        Indices into ``findings``, at most :data:`MAX_VERIFICATION_FINDINGS`.
    """
    if mode is ReviewVerifyMode.OFF:
        return ()
    ranked: list[tuple[int, int, int]] = []
    for index, finding in enumerate(findings):
        if finding.is_question:
            continue
        is_p1 = finding.severity is Severity.P1
        low = finding.confidence.strip().lower() == _LOW_CONFIDENCE
        if is_p1:
            ranked.append((0, 0, index))
        elif low and mode is ReviewVerifyMode.P1_AND_LOW_CONFIDENCE:
            band = {Severity.P2: 1, Severity.P3: 2}.get(finding.severity, 2)
            ranked.append((1, band, index))
    ranked.sort()
    return tuple(index for _, _, index in ranked[:MAX_VERIFICATION_FINDINGS])


def verification_degradations(
    *,
    summary: VerificationSummary | None,
) -> tuple[CoverageDegradation, ...]:
    """Return the whole-run degradation a failed verification pass records.

    Args:
        summary: The pass result, or ``None`` when the pass did not run.

    Returns:
        One ``VERIFICATION_FAILED`` row at the synthesis sentinel index when
        the pass ran and failed, else nothing.
    """
    if summary is None or not summary.failed:
        return ()
    return (
        CoverageDegradation(
            reason=CoverageDegradationReason.VERIFICATION_FAILED,
            chunk_index=SYNTHESIS_CHUNK_INDEX,
            split=False,
        ),
    )


async def _await_call_until_stop(
    *,
    call: Coroutine[Any, Any, AIResponse],
    stop: asyncio.Event | None,
) -> AIResponse:
    """Await the pass's one provider call, abandoning it on an interrupt.

    Args:
        call: The pending provider call.
        stop: Event set by the run's SIGTERM/SIGINT handler, or ``None``.

    Returns:
        The provider response.

    Raises:
        _VerificationInterruptedError: When the stop event won the race.
    """
    if stop is None:
        return await call
    if stop.is_set():
        # Already stopping: do not start provider-side work only to cancel it.
        call.close()
        raise _VerificationInterruptedError
    call_task = asyncio.ensure_future(call)
    stop_task = asyncio.ensure_future(stop.wait())
    try:
        done, _pending = await asyncio.wait(
            {call_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done and stop.is_set() and not call_task.done():
            raise _VerificationInterruptedError
        return await call_task
    finally:
        for task in (call_task, stop_task):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


def _failed(
    *,
    findings: Sequence[ReviewFinding],
    selected: int,
    usage: tuple[int, int, float] = (0, 0, 0.0),
) -> VerificationPass:
    """Return the fail-soft result: findings unchanged, pass marked failed.

    Args:
        findings: The round's findings, returned as they were.
        selected: How many findings the pass tried to verify.
        usage: Tokens and cost the failed call still consumed.

    Returns:
        The pass result.
    """
    input_tokens, output_tokens, cost_estimate = usage
    return VerificationPass(
        findings=tuple(findings),
        summary=VerificationSummary(
            enabled=True,
            selected=selected,
            failed=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost_estimate,
        ),
    )


async def run_verification_pass(
    *,
    request: VerificationPassRequest,
) -> VerificationPass:
    """Verify the round's selected findings with one provider call.

    Args:
        request: The pass's inputs.

    Returns:
        The pass result. Any failure — a budget stop, a provider error, an
        interrupt, an unreadable answer — comes back fail-soft: the findings
        are returned unchanged and the summary says the pass failed, never
        an exception. With nothing selected the pass makes no call and the
        summary says so.
    """
    findings = tuple(request.findings)
    indices = select_for_verification(findings=findings, mode=request.mode)
    if not indices:
        return VerificationPass(
            findings=findings,
            summary=VerificationSummary(
                enabled=request.mode is not ReviewVerifyMode.OFF,
            ),
        )
    metadata = request.context.pr_metadata
    boundary = make_boundary_marker()
    user_prompt = REVIEW_VERIFICATION_USER_PROMPT_TEMPLATE.format(
        finding_count=len(indices),
        boundary=boundary,
        pr_title=redact_prompt_text(
            text=metadata.title if metadata else "Local changes",
            source="PR title",
        ),
        findings=render_verification_findings(
            findings=findings,
            indices=indices,
            source=request.repo_source,
            boundary=boundary,
            allowed_paths=(
                request.allowed_paths
                if request.allowed_paths is not None
                else frozenset(f.path for f in request.context.changed_files)
            ),
        ),
    )
    logger.info(
        "Verification pass: {} of {} findings selected ({} prompt tokens)",
        len(indices),
        len(findings),
        estimate_tokens(user_prompt),
    )
    try:
        request.budget.check()
        response = await _await_call_until_stop(
            call=provider_call.call_ai(
                provider=request.provider,
                ai_config=request.ai_config,
                system_prompt=REVIEW_VERIFICATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                budget=request.budget,
                max_tokens=_MAX_TOKENS,
                repo_root=request.repo_root or None,
                use_one_shot=request.use_one_shot,
                cli_schema=cli_schema_for_verification(
                    transport=request.ai_config.transport,
                ),
            ),
            stop=request.stop,
        )
    except _VerificationInterruptedError:
        logger.warning(
            "The verification pass was interrupted; findings kept unverified.",
        )
        return _failed(findings=findings, selected=len(indices))
    except AITurnLimitError as exc:
        logger.warning("The verification pass hit its per-call turn limit.")
        return _failed(
            findings=findings,
            selected=len(indices),
            usage=(exc.input_tokens, exc.output_tokens, exc.cost_estimate),
        )
    except Exception:
        # Deliberately broad, like the synthesis pass: this call is optional
        # and additive, so nothing it raises — a cost-cap stop included — may
        # turn a completed review into a failed or partial one.
        logger.opt(exception=True).warning(
            "The verification pass failed; findings kept unverified.",
        )
        return _failed(findings=findings, selected=len(indices))

    usage = (response.input_tokens, response.output_tokens, response.cost_estimate)
    verdicts = parse_verification_answer(content=response.content, count=len(indices))
    if not verdicts:
        # ``None`` is an off-schema answer; an empty mapping is an answer
        # with no usable verdict for any selected finding. Both leave every
        # selected finding unverified, so both are the failed pass.
        logger.warning("The verification pass answered outside its schema.")
        return _failed(findings=findings, selected=len(indices), usage=usage)
    kept, confirmed, refuted, downgraded, refutations = apply_verification_verdicts(
        findings=findings,
        indices=indices,
        verdicts=verdicts,
    )
    unanswered = len(indices) - confirmed - refuted - downgraded
    if unanswered:
        logger.warning(
            "The verification pass left {} of {} selected findings unanswered.",
            unanswered,
            len(indices),
        )
    return VerificationPass(
        findings=kept,
        summary=VerificationSummary(
            enabled=True,
            selected=len(indices),
            confirmed=confirmed,
            refuted=refuted,
            downgraded=downgraded,
            unanswered=unanswered,
            refutations=refutations,
            input_tokens=usage[0],
            output_tokens=usage[1],
            cost_estimate=usage[2],
        ),
    )
