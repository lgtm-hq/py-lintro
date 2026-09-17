"""Provider call and response handling for a single review chunk.

One chunk's main provider round-trip lives here: build the prompt and call the
model, parse the answer (recovering prose instead of discarding it), and
convert the parsed payload into a
:class:`~lintro.ai.review.merge.ChunkReviewPartial` the merge layer folds
together (issue #2301).

A call that exhausts the provider's output-token ceiling is not retried here:
:mod:`lintro.ai.review.chunk_split_retry` answers it by splitting the chunk in
two and reviewing each half, recording the split as a coverage degradation so
a re-reviewed chunk can never present as an untouched one. No per-call
findings cap exists (lintro-ops milestone 0, decision A): a chunk reports
every finding it has. The parse ladder never drops a paid-for answer either:
a non-JSON reply becomes unstructured findings rather than an error.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.ai.cli_schemas import cli_schema_for_review
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AICostBudgetExceededError,
    AIError,
    AIProviderNotRegisteredError,
)
from lintro.ai.prompts.review import (
    REVIEW_OUTPUT_SCHEMA,
    REVIEW_SCHEMA_REMINDER_TEMPLATE,
    REVIEW_SYSTEM,
)
from lintro.ai.raw_response import persist_raw_response
from lintro.ai.review import provider_call
from lintro.ai.review.confirmation_filter import drop_confirmation_findings
from lintro.ai.review.diff_gate import (
    DEFAULT_NEAR_LINES,
    DiffGate,
    DiffGateCounts,
    hunks_from_diff,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.finding_parser import parse_findings, parse_flagged_files
from lintro.ai.review.merge import (
    ChunkReviewPartial,
    parse_review_response,
)
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.prompts import (
    PromptInputs,
    build_git_native_review_prompt,
    build_review_prompt,
)
from lintro.ai.review.repo_context import RepoContextSource, build_repo_context
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
    "ChunkCallResult",
    "ChunkReviewRequest",
    "invoke_chunk_review",
    "merge_response_usage",
    "provider_can_run_commands",
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
        chunk_index: Zero-based position of the chunk in the run, stamped on
            any recorded coverage degradation.
        repo_context: Cached head-side reader for the read-only repository
            context section (#2714); ``None`` renders no section.
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
    chunk_index: int
    repo_context: RepoContextSource | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkCallResult:
    """What one chunk's main provider call produced.

    Attributes:
        response: The provider response whose usage the chunk is charged.
        elapsed: Wall-clock seconds the call took.
        coverage_degradations: Limits the call itself applied before asking
            (today only the delegated-diff fallback, #2685); the chunk pass
            records them on the partial.
        context_tokens: Estimated tokens of the read-only repository context
            the prompt carried (#2714), for the run's usage record.
    """

    response: AIResponse
    elapsed: float
    coverage_degradations: tuple[CoverageDegradation, ...] = ()
    context_tokens: int = 0


def provider_can_run_commands(provider: BaseAIProvider) -> bool:
    """Return whether the provider's bounded CLI agent can run a shell command.

    A provider without registered metadata, or without declared CLI bounds,
    is assumed able to: only a declared bound withholds the shell (#2685).

    Args:
        provider: The configured provider instance.

    Returns:
        False when the provider's read-only tool surface has no shell.
    """
    from lintro.ai.registry import metadata_for

    try:
        bounds = metadata_for(provider.name).cli_bounds
    except AIProviderNotRegisteredError:
        return True
    return bounds is None or bounds.shell_available


async def invoke_chunk_review(
    *,
    request: ChunkReviewRequest,
) -> ChunkCallResult:
    """Build the chunk prompt and call the provider once.

    Args:
        request: The chunk, prompt material, provider handles and limits for
            this call.

    A failed provider call raises as is: an ``AICostBudgetExceededError`` when
    the session cost ceiling is hit, otherwise the ``AIError`` the provider
    raised, including on output-token exhaustion, which
    :mod:`lintro.ai.review.chunk_split_retry` recognises and answers by
    splitting the chunk.

    Returns:
        The provider response and the wall-clock seconds it took.
    """
    ai_config = request.ai_config
    use_git_native = ai_config.transport == AITransport.CLI
    started = time.monotonic()
    prompt_inputs = PromptInputs(
        chunk=request.chunk,
        context=request.context,
        checklist_text=request.checklist_text,
        checklist_count=request.checklist_count,
        interaction_paths=request.interaction_paths,
        lint_results=request.lint_results,
        extra_checklist=request.extra_checklist,
        strictness_section=request.strictness_section,
        repo_context=(
            build_repo_context(
                chunk=request.chunk,
                context=request.context,
                source=request.repo_context,
                budget_tokens=ai_config.review_context_tokens,
            )
            if request.repo_context is not None
            else None
        ),
    )
    degradations: tuple[CoverageDegradation, ...] = ()
    if use_git_native:
        embed_diff = estimate_tokens(request.chunk.diff) <= max(
            request.diff_budget,
            1,
        )
        if (
            not embed_diff
            and ai_config.review_allow_unredacted_git_native
            and not provider_can_run_commands(request.provider)
        ):
            # The opt-in asks the agent to run `git diff` itself, but the
            # bounded read-only tool surface has no shell (#2685), so the
            # prompt would be unexecutable and the call would only burn its
            # turn limit. Take the embedded (redacted) path instead and
            # record that the opt-in was not honoured.
            logger.warning(
                "Chunk {} exceeds the diff budget but the {} CLI's read-only "
                "tools cannot run git diff; embedding the redacted diff "
                "instead of delegating it (review_allow_unredacted_git_native "
                "ignored).",
                request.chunk_index,
                request.provider.name,
            )
            embed_diff = True
            degradations = (
                CoverageDegradation(
                    reason=CoverageDegradationReason.DELEGATED_DIFF_EMBEDDED,
                    chunk_index=request.chunk_index,
                    split=False,
                ),
            )
        system_prompt, user_prompt = build_git_native_review_prompt(
            inputs=prompt_inputs,
            embed_diff=embed_diff,
            allow_unredacted_git_native=(ai_config.review_allow_unredacted_git_native),
        )
    else:
        system_prompt, user_prompt = build_review_prompt(inputs=prompt_inputs)
    response = await provider_call.call_ai(
        provider=request.provider,
        ai_config=ai_config,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        budget=request.budget,
        repo_root=request.repo_root or None,
        use_one_shot=request.use_one_shot,
        cli_schema=cli_schema_for_review(transport=ai_config.transport),
    )
    return ChunkCallResult(
        response=response,
        elapsed=time.monotonic() - started,
        coverage_degradations=degradations,
        context_tokens=(
            prompt_inputs.repo_context.tokens
            if prompt_inputs.repo_context is not None
            else 0
        ),
    )


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
        retry_response = await provider_call.call_ai(
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
    chunk: ReviewChunk | None = None,
    near_lines: int = DEFAULT_NEAR_LINES,
) -> ChunkReviewPartial:
    """Convert a parsed chunk payload to a chunk partial result.

    The chunk contract is findings only (lintro-ops milestone 0, decision
    A): ``findings`` and the ``flagged_files`` re-read requests are read and
    every other key is ignored, so an older model that still emits a
    summary, checklist or per-file overview degrades to nothing rather than
    failing the chunk.

    Findings whose body says they are not a defect are dropped here (#2430),
    and, when the chunk is known, findings outside its hunks are dropped or
    re-anchored by the diff-bounded gate (#2711) with the counts recorded on
    the partial.

    Args:
        response: Provider response the payload was parsed from.
        payload: Parsed model response for one chunk.
        chunk: The chunk the payload answers; its diff bounds the findings.
        near_lines: Re-anchor distance for the diff-bounded gate.

    Returns:
        The chunk partial result.
    """
    gate = (
        DiffGate(
            hunks=hunks_from_diff(diff=chunk.diff),
            near_lines=near_lines,
        )
        if chunk is not None
        else None
    )
    findings = drop_confirmation_findings(
        findings=parse_findings(
            raw_findings=payload.get("findings", []),
            diff_gate=gate,
        ),
    )
    flagged_files = parse_flagged_files(raw_flags=payload.get("flagged_files"))
    return ChunkReviewPartial(
        findings=findings,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_estimate=response.cost_estimate,
        turns=response.turns,
        flagged_files=flagged_files,
        diff_gate=gate.counts if gate is not None else DiffGateCounts(),
    )
