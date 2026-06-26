"""Review orchestrator for AI diff-based code review."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.ai.budget import CostBudget
from lintro.ai.fallback import complete_with_fallback
from lintro.ai.model_pricing import (
    calculate_available_diff_tokens,
    get_context_window,
)
from lintro.ai.prompts.review import (
    REVIEW_ADVERSARIAL_SWEEP_TEMPLATE,
    REVIEW_GENERATE_QUESTIONS_TEMPLATE,
    REVIEW_OUTPUT_SCHEMA,
    REVIEW_SYSTEM,
    REVIEW_USER_PROMPT_TEMPLATE,
    format_changed_files_for_prompt,
    format_lint_results_section,
)
from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.paths_registry import generate_interaction_paths
from lintro.ai.review.progress import NullReviewProgress, ReviewProgressCallback
from lintro.ai.token_budget import estimate_tokens

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import AIResponse, BaseAIProvider
    from lintro.ai.review.models.checklist_item import ChecklistItem
    from lintro.ai.review.models.file_classification import FileClassification
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.models.review_context import ReviewContext

__all__ = [
    "build_review_prompt",
    "merge_checklist_answers",
    "merge_findings",
    "merge_review_results",
    "parse_review_response",
    "run_review",
    "strip_json_fences",
]

_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)
_PROMPT_OVERHEAD_TOKENS = 12_000


@dataclass(frozen=True, slots=True)
class _ChunkReviewPartial:
    """Intermediate review result for one chunk."""

    summary: str
    checklist: tuple[ChecklistAnswer, ...]
    findings: tuple[ReviewFinding, ...]
    input_tokens: int
    output_tokens: int
    cost_estimate: float


def run_review(
    context: ReviewContext,
    *,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    depth: int = 1,
    checklist_items: list[ChecklistItem],
    checklist_text: str,
    classifications: list[FileClassification],
    context_window_override: int | None = None,
    lint_results: str | None = None,
    progress: ReviewProgressCallback | None = None,
) -> ReviewResult:
    """Execute an AI diff review with depth-controlled passes.

    Args:
        context: Collected review diff context.
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget, and fallbacks.
        depth: Review depth level (1-3).
        checklist_items: Selected checklist items for the review.
        checklist_text: Pre-formatted checklist prompt text.
        classifications: Domain classifications for changed files.
        context_window_override: Optional explicit context window override.
        lint_results: Optional lint digest for ``--with-lint`` integration.
        progress: Optional progress callback for live status updates.

    Returns:
        Complete review result with metadata, checklist, and findings.

    Raises:
        ValueError: If ``depth`` is outside the supported range (1-3).
    """
    if depth < 1 or depth > 3:
        raise ValueError(f"depth must be between 1 and 3, got {depth}")

    if not context.changed_files and not context.unified_diff.strip():
        return _empty_review_result(
            context=context,
            provider=provider,
            depth=depth,
            checklist_items=checklist_items,
            context_window_override=context_window_override,
        )

    context_window = get_context_window(
        model=provider.model_name,
        override=context_window_override,
    )
    prompt_overhead = _estimate_prompt_overhead(
        context=context,
        checklist_text=checklist_text,
        classifications=classifications,
        lint_results=lint_results,
    )
    diff_budget = calculate_available_diff_tokens(
        context_window=context_window,
        prompt_overhead=prompt_overhead,
    )
    chunking = chunk_review_context(
        context=context,
        max_tokens=max(diff_budget, 1),
        classifications=classifications,
    )
    chunks = chunking.chunks or [
        _single_chunk_from_context(context=context),
    ]

    tracker = progress or NullReviewProgress()
    budget = CostBudget(max_cost_usd=ai_config.max_cost_usd)
    partials: list[_ChunkReviewPartial] = []

    tracker.on_start(total_chunks=len(chunks), depth=depth)

    for chunk_index, chunk in enumerate(chunks):
        budget.check()
        tracker.on_chunk_start(
            chunk_index=chunk_index,
            files=list(chunk.files),
        )
        partial = _review_chunk(
            chunk=chunk,
            context=context,
            provider=provider,
            ai_config=ai_config,
            depth=depth,
            checklist_text=checklist_text,
            checklist_count=len(checklist_items),
            classifications=classifications,
            lint_results=lint_results,
            budget=budget,
            progress=tracker,
            chunk_index=chunk_index,
        )
        partials.append(partial)
        tracker.on_chunk_done(chunk_index=chunk_index)

    merged = merge_review_results(partials=partials)
    total_findings = len(merged.findings) if hasattr(merged, "findings") else 0
    tracker.on_complete(total_findings=total_findings)
    total_input = sum(partial.input_tokens for partial in partials)
    total_output = sum(partial.output_tokens for partial in partials)
    total_cost = sum(partial.cost_estimate for partial in partials)

    metadata = ReviewMetadata(
        model=provider.model_name,
        provider=provider.name,
        context_window=context_window,
        depth=depth,
        chunks_total=len(chunks),
        chunks_current=len(chunks),
        files_reviewed=len(context.changed_files),
        files_total=len(context.changed_files),
        checklist_items=len(checklist_items),
        token_usage={
            "prompt": total_input,
            "completion": total_output,
            "total": total_input + total_output,
        },
        cost_estimate_usd=total_cost,
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        timestamp=datetime.now(tz=UTC).isoformat(),
    )

    return ReviewResult(
        metadata=metadata,
        summary=merged.summary,
        checklist=merged.checklist,
        findings=merged.findings,
    )


def build_review_prompt(
    *,
    chunk: ReviewChunk,
    context: ReviewContext,
    checklist_text: str,
    checklist_count: int,
    interaction_paths: str,
    lint_results: str | None = None,
    extra_checklist: str = "",
) -> tuple[str, str]:
    """Build system and user prompts for a review chunk.

    Args:
        chunk: Semantic diff chunk to review.
        context: Full review context for PR metadata and file list.
        checklist_text: Formatted checklist for the prompt.
        checklist_count: Number of checklist items in the prompt.
        interaction_paths: Domain-triggered interaction path text.
        lint_results: Optional lint digest for prompt injection.
        extra_checklist: Additional generated checklist rows for depth 2.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    pr_title = context.pr_metadata.title if context.pr_metadata else "Local changes"
    pr_summary = context.pr_metadata.body if context.pr_metadata else "(no PR summary)"
    changed_files = [file for file in context.changed_files if file.path in chunk.files]
    combined_checklist = checklist_text
    if extra_checklist.strip():
        combined_checklist = f"{checklist_text}\n\n{extra_checklist.strip()}"
        checklist_count += extra_checklist.strip().count("\n") + (
            1 if extra_checklist.strip() else 0
        )

    user_prompt = REVIEW_USER_PROMPT_TEMPLATE.format(
        pr_title=pr_title,
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        pr_summary=pr_summary,
        deferred_scope_section="",
        external_review_section="",
        changed_file_count=len(changed_files),
        changed_files=format_changed_files_for_prompt(files=changed_files),
        interaction_paths=interaction_paths,
        checklist_count=checklist_count,
        checklist=combined_checklist,
        diff=chunk.diff,
        lint_results_section=format_lint_results_section(digest=lint_results),
        output_schema=REVIEW_OUTPUT_SCHEMA,
    )
    return REVIEW_SYSTEM, user_prompt


def strip_json_fences(*, content: str) -> str:
    """Strip markdown JSON code fences from model output.

    Args:
        content: Raw model response text.

    Returns:
        JSON string suitable for ``json.loads``.
    """
    stripped = content.strip()
    match = _JSON_FENCE_PATTERN.search(stripped)
    if match is not None:
        return match.group(1).strip()
    return stripped


def parse_review_response(*, content: str) -> dict[str, Any]:
    """Parse and validate AI review JSON response.

    Args:
        content: Raw or fenced JSON model response.

    Returns:
        Parsed review response dictionary.

    Raises:
        ValueError: When JSON is invalid or missing required keys.
    """
    json_text = strip_json_fences(content=content)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid review JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Review response must be a JSON object")

    for key in ("summary", "checklist", "findings"):
        if key not in payload:
            raise ValueError(f"Review response missing required key: {key}")

    return payload


def merge_findings(
    *,
    findings_groups: list[tuple[ReviewFinding, ...]],
) -> tuple[ReviewFinding, ...]:
    """Merge findings from multiple chunks, deduplicating by location.

    Args:
        findings_groups: Finding tuples from each chunk/pass.

    Returns:
        Deduplicated findings preserving first-seen order.
    """
    merged: list[ReviewFinding] = []
    seen: set[tuple[str, int, str]] = set()
    for group in findings_groups:
        for finding in group:
            key = (finding.file, finding.line, finding.title)
            if key in seen:
                continue
            seen.add(key)
            merged.append(finding)
    return tuple(merged)


def merge_checklist_answers(
    *,
    checklist_groups: list[tuple[ChecklistAnswer, ...]],
) -> tuple[ChecklistAnswer, ...]:
    """Merge checklist answers with yes winning over no.

    Args:
        checklist_groups: Checklist answer tuples from each chunk/pass.

    Returns:
        Merged checklist answers keyed by checklist id.
    """
    by_id: dict[int, ChecklistAnswer] = {}
    for group in checklist_groups:
        for answer in group:
            existing = by_id.get(answer.id)
            if existing is None:
                by_id[answer.id] = answer
                continue
            if answer.answer.lower() == "yes" or existing.answer.lower() != "yes":
                by_id[answer.id] = answer
    return tuple(sorted(by_id.values(), key=lambda item: item.id))


def merge_review_results(
    *,
    partials: list[_ChunkReviewPartial],
) -> ReviewResult:
    """Merge partial chunk results into a single review result shell.

    Args:
        partials: Partial results from each chunk.

    Returns:
        Review result without metadata (caller attaches metadata).
    """
    if not partials:
        return ReviewResult(
            metadata=_placeholder_metadata(),
            summary="No review output.",
            checklist=(),
            findings=(),
        )

    summaries = [partial.summary for partial in partials if partial.summary.strip()]
    summary = summaries[0] if len(summaries) == 1 else "\n\n".join(summaries[:3])

    return ReviewResult(
        metadata=_placeholder_metadata(),
        summary=summary,
        checklist=merge_checklist_answers(
            checklist_groups=[partial.checklist for partial in partials],
        ),
        findings=merge_findings(
            findings_groups=[partial.findings for partial in partials],
        ),
    )


def _review_chunk(
    *,
    chunk: ReviewChunk,
    context: ReviewContext,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    depth: int,
    checklist_text: str,
    checklist_count: int,
    classifications: list[FileClassification],
    lint_results: str | None,
    budget: CostBudget,
    progress: ReviewProgressCallback | None = None,
    chunk_index: int = 0,
) -> _ChunkReviewPartial:
    """Run depth-controlled review for a single chunk."""
    tracker = progress or NullReviewProgress()
    interaction_paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=chunk.files,
    )
    extra_checklist = ""
    if depth >= 2:
        tracker.on_step(chunk_index=chunk_index, step="generating questions")
        extra_checklist = _generate_extra_checklist(
            chunk=chunk,
            context=context,
            provider=provider,
            ai_config=ai_config,
            budget=budget,
        )

    tracker.on_step(chunk_index=chunk_index, step="reviewing")
    system_prompt, user_prompt = build_review_prompt(
        chunk=chunk,
        context=context,
        checklist_text=checklist_text,
        checklist_count=checklist_count,
        interaction_paths=interaction_paths,
        lint_results=lint_results,
        extra_checklist=extra_checklist,
    )
    response = _call_provider(
        provider=provider,
        ai_config=ai_config,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        budget=budget,
    )
    payload = parse_review_response(content=response.content)
    partial = _payload_to_partial(response=response, payload=payload)

    if depth >= 3:
        tracker.on_step(chunk_index=chunk_index, step="adversarial sweep")
        adversarial = _run_adversarial_pass(
            chunk=chunk,
            provider=provider,
            ai_config=ai_config,
            prior_findings=partial.findings,
            budget=budget,
        )
        partial = replace(
            partial,
            findings=merge_findings(
                findings_groups=[partial.findings, adversarial.findings],
            ),
            input_tokens=partial.input_tokens + adversarial.input_tokens,
            output_tokens=partial.output_tokens + adversarial.output_tokens,
            cost_estimate=partial.cost_estimate + adversarial.cost_estimate,
        )

    return partial


def _generate_extra_checklist(
    *,
    chunk: ReviewChunk,
    context: ReviewContext,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    budget: CostBudget,
) -> str:
    """Generate depth-2 domain-specific checklist questions."""
    changed_files = format_changed_files_for_prompt(
        files=[file for file in context.changed_files if file.path in chunk.files],
    )
    prompt = REVIEW_GENERATE_QUESTIONS_TEMPLATE.format(
        diff=chunk.diff,
        changed_files=changed_files,
    )
    response = _call_provider(
        provider=provider,
        ai_config=ai_config,
        system_prompt="You generate review checklist questions.",
        user_prompt=prompt,
        budget=budget,
        max_tokens=1024,
    )
    try:
        payload = json.loads(strip_json_fences(content=response.content))
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse generated questions; skipping depth-2 extras")
        return ""

    questions = payload.get("generated_questions", [])
    if not isinstance(questions, list):
        return ""

    lines: list[str] = []
    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            continue
        question = item.get("question")
        if isinstance(question, str) and question.strip():
            lines.append(f"G{index}. [generated] {question.strip()}")
    return "\n".join(lines)


def _run_adversarial_pass(
    *,
    chunk: ReviewChunk,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    prior_findings: tuple[ReviewFinding, ...],
    budget: CostBudget,
) -> _ChunkReviewPartial:
    """Run depth-3 adversarial sweep for missed findings."""
    prior_json = json.dumps(
        [
            {
                "severity": finding.severity,
                "file": finding.file,
                "line": finding.line,
                "title": finding.title,
            }
            for finding in prior_findings
        ],
    )
    prompt = REVIEW_ADVERSARIAL_SWEEP_TEMPLATE.format(
        prior_findings_json=prior_json,
        diff=chunk.diff,
    )
    response = _call_provider(
        provider=provider,
        ai_config=ai_config,
        system_prompt=REVIEW_SYSTEM,
        user_prompt=prompt,
        budget=budget,
    )
    try:
        payload = json.loads(strip_json_fences(content=response.content))
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse adversarial sweep response")
        return _ChunkReviewPartial(
            summary="",
            checklist=(),
            findings=(),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_estimate=response.cost_estimate,
        )

    findings_raw = payload.get("findings", [])
    findings = _parse_findings(raw_findings=findings_raw)
    return _ChunkReviewPartial(
        summary="",
        checklist=(),
        findings=findings,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_estimate=response.cost_estimate,
    )


def _call_provider(
    *,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    system_prompt: str,
    user_prompt: str,
    budget: CostBudget,
    max_tokens: int | None = None,
) -> AIResponse:
    """Call the AI provider with retry/fallback and budget tracking."""
    tokens = max_tokens if max_tokens is not None else ai_config.max_tokens
    response = complete_with_fallback(
        provider,
        user_prompt,
        fallback_models=list(ai_config.fallback_models),
        system=system_prompt,
        max_tokens=tokens,
        timeout=ai_config.api_timeout,
    )
    budget.record(response.cost_estimate)
    return response


def _payload_to_partial(
    *,
    response: AIResponse,
    payload: dict[str, Any],
) -> _ChunkReviewPartial:
    """Convert parsed JSON payload to a chunk partial result."""
    summary = payload.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)

    checklist = _parse_checklist(raw_checklist=payload.get("checklist", []))
    findings = _parse_findings(raw_findings=payload.get("findings", []))

    return _ChunkReviewPartial(
        summary=summary,
        checklist=checklist,
        findings=findings,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_estimate=response.cost_estimate,
    )


def _parse_checklist(*, raw_checklist: object) -> tuple[ChecklistAnswer, ...]:
    """Parse checklist answers from AI JSON."""
    if not isinstance(raw_checklist, list):
        return ()
    answers: list[ChecklistAnswer] = []
    for item in raw_checklist:
        if not isinstance(item, dict):
            continue
        answer_id = item.get("id")
        answer = item.get("answer", "no")
        evidence = item.get("evidence", "")
        if not isinstance(answer_id, int):
            continue
        if not isinstance(answer, str):
            answer = str(answer)
        if not isinstance(evidence, str):
            evidence = str(evidence)
        answers.append(
            ChecklistAnswer(
                id=answer_id,
                answer=answer.lower(),
                evidence=evidence,
            ),
        )
    return tuple(answers)


def _parse_findings(*, raw_findings: object) -> tuple[ReviewFinding, ...]:
    """Parse findings from AI JSON."""
    if not isinstance(raw_findings, list):
        return ()
    findings: list[ReviewFinding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        line = item.get("line", 0)
        if not isinstance(line, int):
            try:
                line = int(line)
            except (TypeError, ValueError):
                line = 0
        checklist_ids_raw = item.get("checklist_ids", [])
        checklist_ids: tuple[int, ...] = ()
        if isinstance(checklist_ids_raw, list):
            checklist_ids = tuple(
                checklist_id
                for checklist_id in checklist_ids_raw
                if isinstance(checklist_id, int)
            )
        findings.append(
            ReviewFinding(
                severity=str(item.get("severity", "P3")),
                category=str(item.get("category", "logic-bug")),
                file=str(item.get("file", "")),
                line=line,
                title=str(item.get("title", "")),
                description=str(item.get("description", "")),
                cause=str(item.get("cause", "")),
                fix=str(item.get("fix", "")),
                confidence=str(item.get("confidence", "medium")),
                checklist_ids=checklist_ids,
            ),
        )
    return tuple(findings)


def _estimate_prompt_overhead(
    *,
    context: ReviewContext,
    checklist_text: str,
    classifications: list[FileClassification],
    lint_results: str | None,
) -> int:
    """Estimate non-diff prompt token overhead."""
    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=[file.path for file in context.changed_files],
    )
    overhead_text = "\n".join(
        [
            REVIEW_SYSTEM,
            checklist_text,
            paths,
            context.pr_metadata.body if context.pr_metadata else "",
            lint_results or "",
        ],
    )
    estimated = estimate_tokens(overhead_text)
    return max(estimated, _PROMPT_OVERHEAD_TOKENS)


def _single_chunk_from_context(*, context: ReviewContext) -> ReviewChunk:
    """Build a single chunk when chunker returns no groups."""
    return ReviewChunk(
        id=1,
        files=[file.path for file in context.changed_files],
        diff=context.unified_diff,
        relationship="full-diff",
    )


def _empty_review_result(
    *,
    context: ReviewContext,
    provider: BaseAIProvider,
    depth: int,
    checklist_items: list[ChecklistItem],
    context_window_override: int | None,
) -> ReviewResult:
    """Return an empty result when no changes are present."""
    context_window = get_context_window(
        model=provider.model_name,
        override=context_window_override,
    )
    metadata = ReviewMetadata(
        model=provider.model_name,
        provider=provider.name,
        context_window=context_window,
        depth=depth,
        chunks_total=0,
        chunks_current=0,
        files_reviewed=0,
        files_total=0,
        checklist_items=len(checklist_items),
        token_usage={"prompt": 0, "completion": 0, "total": 0},
        cost_estimate_usd=0.0,
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        timestamp=datetime.now(tz=UTC).isoformat(),
    )
    return ReviewResult(
        metadata=metadata,
        summary="No changes found to review.",
        checklist=(),
        findings=(),
    )


def _placeholder_metadata() -> ReviewMetadata:
    """Return placeholder metadata for merge-only results."""
    return ReviewMetadata(
        model="",
        provider="",
        context_window=0,
        depth=0,
        chunks_total=0,
        chunks_current=0,
        files_reviewed=0,
        files_total=0,
        checklist_items=0,
    )
