"""Review session options and stop-condition helpers.

The options object is the single carrier for everything a review run needs
beyond its context: :func:`lintro.ai.review.orchestrator.run_review` builds one
from its keyword arguments and every layer below takes the object instead of
re-threading the same twenty keywords by hand (issue #2301).

The stop-condition helpers answer whether an exception that ended a run is a
graceful stop (cost cap, timeout) that should be reported as a partial review,
or a genuine failure that must propagate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.exceptions import AICostBudgetExceededError
from lintro.ai.review.errors_taxonomy import (
    ReviewErrorKind,
    classify_provider_error,
    resolve_cause_text,
)
from lintro.ai.review.exceptions import ReviewExecutionError

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path

    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.review.custom_agents import CustomAgentSpec
    from lintro.ai.review.models.checklist_item import ChecklistItem
    from lintro.ai.review.models.file_classification import FileClassification
    from lintro.ai.review.models.review_state import ReviewState
    from lintro.ai.review.progress import ReviewProgressCallback
    from lintro.ai.review.sensitivity import ReviewSensitivityPolicy
    from lintro.config.review_config import ReviewSynthesisConfig

__all__ = [
    "ReviewSessionOptions",
    "aborted_before_completion",
    "cost_cap_reason",
    "is_cost_cap_stop",
    "is_timeout_stop",
    "timeout_reason",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewSessionOptions:
    """Everything a review run needs beyond its :class:`ReviewContext`.

    Attributes:
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget, and fallbacks.
        checklist_items: Selected checklist items for the review.
        checklist_text: Pre-formatted checklist prompt text.
        classifications: Domain classifications for changed files.
        depth: Review depth level (1-3).
        context_window_override: Optional explicit context window override.
        lint_results: Optional lint digest for ``--with-lint`` integration.
        progress: Optional progress callback for live status updates.
        sensitivity: Sensitivity preset controlling prompts and finding
            filters. ``None`` selects the balanced default.
        force_semantic_chunking: When True, skip the single-chunk fast path.
        timeout: Optional per-call timeout override in seconds.
        custom_agents: Discovered user-defined review agents (issue #1245).
            Agents are scoped to the changed files their globs match; each
            scoped agent adds one provider call against the same run budget.
        run_builtin_checklist: When False, skip the built-in checklist passes
            and run only the custom agents (``review.custom_agents: only``).
        workspace_root: Optional workspace root used to build providers for
            agents that declare a ``model`` override.
        context_collection_seconds: Wall-clock seconds the caller spent in
            ``collect_review_context`` (recorded in ``phase_timings``).
        prior_state: Artifact or local-ledger state from a previous round.
        force_full: Discard carried coverage (``--full``).
        enforce_cost_cap: When True, honor ``ai.max_cost_usd`` and serialize
            chunk calls so concurrency cannot violate queue order.
        stop: Optional event set to persist and halt (tests inject this;
            production uses SIGTERM/SIGINT via ``install_review_interrupt``).
        synthesis: Cross-chunk synthesis configuration (#2269). ``None`` or a
            disabled config means no extra pass runs.
    """

    provider: BaseAIProvider
    ai_config: AIConfig
    checklist_items: list[ChecklistItem]
    checklist_text: str
    classifications: list[FileClassification]
    depth: int = 1
    context_window_override: int | None = None
    lint_results: str | None = None
    progress: ReviewProgressCallback | None = None
    sensitivity: ReviewSensitivityPolicy | None = None
    force_semantic_chunking: bool = False
    timeout: float | None = None
    custom_agents: tuple[CustomAgentSpec, ...] = ()
    run_builtin_checklist: bool = True
    workspace_root: Path | None = None
    context_collection_seconds: float = 0.0
    prior_state: ReviewState | None = None
    force_full: bool = False
    enforce_cost_cap: bool = True
    stop: asyncio.Event | None = None
    synthesis: ReviewSynthesisConfig | None = None


def aborted_before_completion(
    *,
    cause: Exception,
    provider: BaseAIProvider,
    chunk_index: int,
    total_chunks: int,
    step: str,
    completed_chunks: int,
) -> ReviewExecutionError:
    """Wrap a mid-run chunk failure, preserving and surfacing the real cause.

    Classifies the underlying provider exception into a canonical
    :class:`~lintro.ai.review.errors_taxonomy.ReviewErrorKind` and logs the real
    cause text so the true failure (e.g. depleted credits) is never lost behind
    the generic "aborted" wrapper.

    Args:
        cause: The underlying provider or parser exception.
        provider: Configured provider, used to resolve provider-aware kinds.
        chunk_index: Zero-based index of the failing chunk.
        total_chunks: Total chunks planned for the review.
        step: Sub-step within the chunk where the failure occurred.
        completed_chunks: Number of chunks completed before the failure.

    Returns:
        A ``ReviewExecutionError`` carrying the resolved kind and cause message.
    """
    kind = classify_provider_error(provider=str(provider.name), error=cause)
    cause_message = resolve_cause_text(error=cause)
    logger.error(
        "Review aborted before all chunks completed on chunk {chunk} during "
        "{step} — kind={kind}, cause: {cause}",
        chunk=chunk_index,
        step=step,
        kind=kind.value,
        cause=cause_message,
    )
    return ReviewExecutionError(
        message="Review aborted before all chunks completed.",
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        step=step,
        completed_chunks=completed_chunks,
        cause_message=cause_message,
        error_kind=kind,
    )


def is_cost_cap_stop(*, exc: BaseException) -> bool:
    """Return whether an exception represents a graceful cost-cap stop.

    The cost cap can surface either as a raw
    :class:`~lintro.ai.exceptions.AICostBudgetExceededError` (when the
    top-of-loop ``budget.check()`` raises) or wrapped inside a
    :class:`~lintro.ai.review.exceptions.ReviewExecutionError` (when an
    intra-chunk check raises and the chunk failure is wrapped). Both cases are
    detected by walking the ``__cause__`` chain so a cost-cap stop is never
    misclassified as a genuine provider error, and vice versa.

    Args:
        exc: The exception raised while reviewing chunks.

    Returns:
        True when the underlying cause is a cost-cap exhaustion.
    """
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, AICostBudgetExceededError):
            return True
        current = current.__cause__
    return False


def cost_cap_reason(*, cap: float | None) -> str:
    """Build the human-readable ``stopped_reason`` for a cost-cap stop.

    Args:
        cap: The configured ``ai.max_cost_usd`` ceiling, if any.

    Returns:
        A message such as ``"cost cap ($0.50) reached"``.
    """
    if cap is None:
        return "cost cap reached"
    return f"cost cap (${cap:.2f}) reached"


def is_timeout_stop(*, exc: BaseException) -> bool:
    """Return whether an exception is a persistable mid-round timeout.

    ADR-0007 / #2154: cap, quota, and timeout all persist coverage and resume.
    A timeout is classified from the wrapped ``ReviewExecutionError`` kind or
    from the provider error text (``timed out`` / ``timeout``).

    Args:
        exc: The exception raised while reviewing chunks.

    Returns:
        True when the underlying cause is a provider or CLI timeout.
    """
    current: BaseException | None = exc
    while current is not None:
        if (
            isinstance(current, ReviewExecutionError)
            and current.error_kind is ReviewErrorKind.TIMEOUT
        ):
            return True
        current = current.__cause__
    if not isinstance(exc, Exception):
        return False
    return classify_provider_error(provider="", error=exc) is ReviewErrorKind.TIMEOUT


def timeout_reason(*, exc: BaseException) -> str:
    """Build the human-readable ``stopped_reason`` for a timeout stop.

    Args:
        exc: The timeout exception (possibly wrapped).

    Returns:
        A short stop reason that names the timeout.
    """
    cause = resolve_cause_text(error=exc) if isinstance(exc, Exception) else str(exc)
    if cause:
        return f"timeout ({cause})"
    return "timeout"
