"""Provider call and response handling for a single review chunk.

One chunk's main provider round-trip lives here: build the prompt and call the
model (retrying once with a tighter findings ceiling when CLI transport hits its
output-token cap), parse the answer (recovering prose instead of discarding it),
and convert the parsed payload into a
:class:`~lintro.ai.review.merge.ChunkReviewPartial` the merge layer folds
together (issue #2301).

Both degradation paths -- the findings cap and the output-exhaustion retry --
are recorded as :class:`CoverageDegradation` entries so a capped chunk can never
present as an unlimited one, and the parse ladder never drops a paid-for answer:
a non-JSON reply becomes unstructured findings rather than an error.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.ai.cli_schemas import cli_schema_for_review
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AICostBudgetExceededError, AIError
from lintro.ai.invoke import call_ai
from lintro.ai.prompts.review import (
    REVIEW_OUTPUT_SCHEMA,
    REVIEW_SCHEMA_REMINDER_TEMPLATE,
    REVIEW_SYSTEM,
)
from lintro.ai.raw_response import persist_raw_response
from lintro.ai.review.cli_limits import (
    is_cli_output_exhaustion,
    tighter_findings_cap,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.finding_parser import parse_findings, parse_flagged_files
from lintro.ai.review.merge import (
    ChunkReviewPartial,
    normalize_checklist_answer_value,
    parse_review_response,
)
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.narrative_parser import parse_narrative, parse_summary_text
from lintro.ai.review.prompts import (
    PromptInputs,
    build_git_native_review_prompt,
    build_review_prompt,
)
from lintro.ai.review.response_recovery import (
    build_schema_reminder_prompt,
    resolve_schema_retry_timeout,
    unstructured_review_payload,
)
from lintro.ai.token_budget import estimate_tokens

if TYPE_CHECKING:
    from lintro.ai.budget import CostBudget
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import AIResponse, BaseAIProvider
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.models.review_context import ReviewContext

__all__ = [
    "ChunkReviewRequest",
    "invoke_chunk_review",
    "merge_response_usage",
    "parse_checklist",
    "parse_review_payload_with_recovery",
    "payload_to_partial",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkReviewRequest:
    """Everything one chunk's main provider call needs.

    The call needs the chunk, the prompt material, the provider handles and the
    per-run limits at once; grouping them keeps the call site a single object
    instead of sixteen keywords threaded through the orchestrator (issue #2301).

    Attributes:
        chunk: The chunk under review.
        context: Collected review diff context.
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget, and timeouts.
        checklist_text: Pre-formatted checklist prompt text.
        checklist_count: Number of checklist items in the prompt.
        interaction_paths: Domain-triggered interaction path text.
        lint_results: Optional lint digest for prompt injection.
        extra_checklist: Additional generated checklist rows for depth 2.
        strictness_section: Pre-formatted strictness prompt section.
        budget: Session cost budget tracker.
        repo_root: Absolute path to the repository under review.
        use_one_shot: When True, avoid durable provider sessions.
        diff_budget: Token budget available for embedded diffs.
        max_findings: Optional per-call findings ceiling.
        chunk_index: Zero-based position of the chunk in the run, stamped on
            any recorded coverage degradation.
    """

    chunk: ReviewChunk
    context: ReviewContext
    provider: BaseAIProvider
    ai_config: AIConfig
    checklist_text: str
    checklist_count: int
    interaction_paths: str
    lint_results: str | None
    extra_checklist: str
    strictness_section: str
    budget: CostBudget
    repo_root: str
    use_one_shot: bool
    diff_budget: int
    max_findings: int | None
    chunk_index: int


async def invoke_chunk_review(
    *,
    request: ChunkReviewRequest,
) -> tuple[AIResponse, float, tuple[CoverageDegradation, ...]]:
    """Build the chunk prompt, call the provider, and retry on output exhaustion.

    When CLI transport hits the ~32k output-token cap mid-JSON, retry once with
    a tighter findings ceiling so the call can finish a complete object (#1967).
    Both the cap itself and the retry are recorded as coverage degradations so
    a capped chunk can never present as an unlimited one (#2003).

    Args:
        request: The chunk, prompt material, provider handles and limits for
            this call.

    Returns:
        The provider response, wall-clock seconds spent on the successful (or
        final) call attempt, and the coverage degradations this chunk incurred.

    Raises:
        AICostBudgetExceededError: When the session cost ceiling is hit.
        AIError: When the provider call fails for a non-retryable reason, or
            when an output-exhaustion retry still fails.
    """
    ai_config = request.ai_config
    use_git_native = ai_config.transport == AITransport.CLI
    findings_cap = request.max_findings
    allow_output_retry = findings_cap is not None and findings_cap > 1
    degradations: list[CoverageDegradation] = []
    if findings_cap is not None:
        degradations.append(
            CoverageDegradation(
                reason=CoverageDegradationReason.FINDINGS_CAP_APPLIED,
                chunk_index=request.chunk_index,
                findings_cap=findings_cap,
            ),
        )
    started = time.monotonic()
    while True:
        prompt_inputs = PromptInputs(
            chunk=request.chunk,
            context=request.context,
            checklist_text=request.checklist_text,
            checklist_count=request.checklist_count,
            interaction_paths=request.interaction_paths,
            lint_results=request.lint_results,
            extra_checklist=request.extra_checklist,
            strictness_section=request.strictness_section,
            max_findings=findings_cap,
        )
        if use_git_native:
            embed_diff = estimate_tokens(request.chunk.diff) <= max(
                request.diff_budget,
                1,
            )
            system_prompt, user_prompt = build_git_native_review_prompt(
                inputs=prompt_inputs,
                embed_diff=embed_diff,
                allow_unredacted_git_native=(
                    ai_config.review_allow_unredacted_git_native
                ),
            )
        else:
            system_prompt, user_prompt = build_review_prompt(inputs=prompt_inputs)
        try:
            response = await call_ai(
                provider=request.provider,
                ai_config=ai_config,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                budget=request.budget,
                repo_root=request.repo_root or None,
                use_one_shot=request.use_one_shot,
                cli_schema=cli_schema_for_review(transport=ai_config.transport),
            )
        except AICostBudgetExceededError:
            raise
        except AIError as exc:
            if (
                allow_output_retry
                and findings_cap is not None
                and is_cli_output_exhaustion(exc)
            ):
                next_cap = tighter_findings_cap(current=findings_cap)
                if next_cap < findings_cap:
                    logger.warning(
                        "CLI review hit an output-token ceiling; retrying "
                        f"chunk with findings cap {findings_cap} → {next_cap}.",
                    )
                    findings_cap = next_cap
                    allow_output_retry = False
                    degradations.append(
                        CoverageDegradation(
                            reason=(
                                CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED
                            ),
                            chunk_index=request.chunk_index,
                            findings_cap=next_cap,
                        ),
                    )
                    # Each attempt gets its own schema-retry window: charging
                    # the retry with the first attempt's elapsed time starves
                    # the recovery the retry exists to provide.
                    started = time.monotonic()
                    continue
            raise
        return response, time.monotonic() - started, tuple(degradations)


async def parse_review_payload_with_recovery(
    *,
    response: AIResponse,
    chunk: ReviewChunk,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    budget: CostBudget,
    repo_root: str,
    use_one_shot: bool,
    elapsed: float,
) -> tuple[AIResponse, dict[str, Any]]:
    """Parse a chunk response, recovering non-JSON answers instead of failing.

    The ladder is: parse (which already extracts JSON embedded in prose) →
    exactly one schema-reminder retry, when the per-call timeout budget still
    allows one → present the prose as unstructured findings with the full text
    preserved. A prose answer normally carries real findings, so discarding it
    as ``invalid_response`` lost work that had already been paid for (#1853).

    Args:
        response: The response from the main chunk call.
        chunk: The chunk under review, used to locate the fallback finding.
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget, and timeouts.
        budget: Session cost budget tracker.
        repo_root: Absolute path to the repository under review.
        use_one_shot: When True, avoid durable provider sessions.
        elapsed: Wall-clock seconds the main chunk call consumed.

    Returns:
        The response whose usage should be attributed to the chunk (the retry's
        usage folded in when a retry ran) and the parsed review payload.

    Raises:
        AICostBudgetExceededError: When the schema-reminder retry hits the cost
            ceiling. That is a graceful stop the caller finalizes a partial
            review on, so it is never recovered as prose.
    """
    try:
        return response, parse_review_response(content=response.content)
    except ValueError as exc:
        first_error = exc

    # Persisted immediately: a successful retry replaces this answer in the
    # payload, and a failed one echoes back only the retry's text, so this is
    # the sole capture of what the model originally produced.
    first_capture = persist_raw_response(
        provider="review",
        stage="parse-failure",
        raw=response.content,
    )
    if first_capture is not None:
        logger.debug(f"Unparseable review response saved to {first_capture}")

    retry_timeout = resolve_schema_retry_timeout(
        api_timeout=ai_config.api_timeout,
        elapsed=elapsed,
    )
    if retry_timeout is None:
        logger.warning(
            "Review response was not valid JSON and the timeout budget left no "
            "room for a schema-reminder retry; recovering it as unstructured "
            f"output ({first_error}).",
        )
        return response, unstructured_review_payload(
            content=response.content,
            files=tuple(chunk.files),
        )

    logger.warning(
        f"Review response was not valid JSON ({first_error}); retrying once "
        f"with a schema reminder (timeout {retry_timeout:.0f}s).",
    )
    reminder = build_schema_reminder_prompt(
        template=REVIEW_SCHEMA_REMINDER_TEMPLATE,
        output_schema=REVIEW_OUTPUT_SCHEMA,
        previous_response=response.content,
    )
    try:
        retry_response = await call_ai(
            provider=provider,
            ai_config=ai_config,
            system_prompt=REVIEW_SYSTEM,
            user_prompt=reminder,
            budget=budget,
            repo_root=repo_root or None,
            use_one_shot=use_one_shot,
            cli_schema=cli_schema_for_review(transport=ai_config.transport),
            timeout=retry_timeout,
        )
    except AICostBudgetExceededError:
        # The cost cap is a graceful stop the caller finalizes a partial review
        # on, not a provider failure: swallowing it here would let the run keep
        # spending past the ceiling.
        raise
    except AIError as retry_exc:
        # The reminder is best-effort: a failed retry must never be worse than
        # not retrying, so the original answer is still recovered.
        logger.warning(f"Schema-reminder retry failed: {retry_exc}")
        return response, unstructured_review_payload(
            content=response.content,
            files=tuple(chunk.files),
        )

    merged = merge_response_usage(first=response, second=retry_response)
    try:
        return merged, parse_review_response(content=retry_response.content)
    except ValueError as retry_error:
        logger.warning(
            f"Schema-reminder retry was still not valid JSON ({retry_error}); "
            "recovering the review as unstructured output.",
        )

    # The retry's answer is the model's latest word; prefer it when it carries
    # text, and fall back to the original answer when the retry came back empty.
    recovered = retry_response.content.strip() or response.content
    return merged, unstructured_review_payload(
        content=recovered,
        files=tuple(chunk.files),
    )


def merge_response_usage(*, first: AIResponse, second: AIResponse) -> AIResponse:
    """Return *second* with *first*'s token and cost usage folded in.

    Args:
        first: The earlier response.
        second: The later response whose content is authoritative.

    Returns:
        A response carrying the combined usage of both calls.
    """
    return replace(
        second,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        cost_estimate=first.cost_estimate + second.cost_estimate,
    )


def payload_to_partial(
    *,
    response: AIResponse,
    payload: dict[str, Any],
) -> ChunkReviewPartial:
    """Convert parsed JSON payload to a chunk partial result.

    Accepts both the extended ``summary`` object (#1907) and the plain summary
    string; narrative fields degrade to ``None``/empty rather than failing the
    chunk. The string shape reaches here from transports that do not enforce
    :data:`~lintro.ai.cli_schemas.REVIEW_CLI_SCHEMA` and from the prose
    recovery payload, not from a schema-constrained CLI-transport reply.

    Args:
        response: Provider response the payload was parsed from.
        payload: Parsed model response for one chunk.

    Returns:
        The chunk partial result.
    """
    raw_summary = payload.get("summary", "")
    summary = parse_summary_text(raw_summary=raw_summary)
    pr_summary, verdict_reasoning, file_assessments = parse_narrative(payload=payload)

    checklist = parse_checklist(raw_checklist=payload.get("checklist", []))
    findings = parse_findings(raw_findings=payload.get("findings", []))
    flagged_files = parse_flagged_files(raw_flags=payload.get("flagged_files"))

    return ChunkReviewPartial(
        summary=summary,
        checklist=checklist,
        findings=findings,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_estimate=response.cost_estimate,
        pr_summary=pr_summary,
        verdict_reasoning=verdict_reasoning,
        file_assessments=file_assessments,
        flagged_files=flagged_files,
    )


def parse_checklist(*, raw_checklist: object) -> tuple[ChecklistAnswer, ...]:
    """Parse checklist answers from AI JSON.

    Args:
        raw_checklist: The ``checklist`` value from a parsed model payload.

    Returns:
        The well-formed checklist answers, in payload order.
    """
    if not isinstance(raw_checklist, list):
        return ()
    answers: list[ChecklistAnswer] = []
    for item in raw_checklist:
        if not isinstance(item, dict):
            continue
        answer_id = item.get("id")
        answer = item.get("answer", "no")
        evidence_raw = item.get("evidence", "")
        if not isinstance(answer_id, int):
            continue
        if not isinstance(answer, str):
            answer = str(answer)
        if evidence_raw is None:
            evidence = ""
        elif isinstance(evidence_raw, str):
            evidence = evidence_raw
        else:
            evidence = str(evidence_raw)
        answers.append(
            ChecklistAnswer(
                id=answer_id,
                answer=normalize_checklist_answer_value(answer=answer),
                evidence=evidence.strip(),
            ),
        )
    return tuple(answers)
