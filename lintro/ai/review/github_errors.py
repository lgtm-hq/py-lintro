"""Error sticky-comment rendering for GitHub AI reviews."""

from __future__ import annotations

from typing import Any

from lintro.ai.review.errors_taxonomy import (
    KIND_COPY,
    ReviewErrorKind,
    classify_provider_error,
    resolve_cause_text,
)
from lintro.ai.review.github_constants import _FOOTER, STICKY_MARKER
from lintro.ai.review.github_render import format_run_mechanics, sanitize_comment_text
from lintro.ai.review.github_sticky import _render_state_block
from lintro.ai.review.models.review_metadata import ReviewMetadata


def format_error_comment(
    *,
    error: Exception,
    provider: str | None = None,
    metadata: ReviewMetadata | None = None,
    prior_runs: list[dict[str, Any]] | None = None,
) -> str:
    """Format a provider/API error as a clear PR comment.

    Classifies the error provider-aware, against the underlying provider cause
    (unwrapping any orchestrator wrapper), into a canonical
    :class:`~lintro.ai.review.errors_taxonomy.ReviewErrorKind` and renders the
    specific sticky for that kind — for example, depleted credits render as a
    clear "top up or lower ai.max_cost_usd" message rather than a generic
    "review aborted". The real underlying cause text is always surfaced.

    Args:
        error: The exception raised during review.
        provider: Provider identifier used for provider-aware classification.
            Falls back to ``metadata.provider`` when omitted.
        metadata: Optional review metadata for a mechanics footer.
        prior_runs: Run records recovered from the previous sticky comment.
            Re-emitted so a transient error does not reset cumulative telemetry.

    Returns:
        Markdown comment body describing the failure and next steps.
    """
    resolved_provider = provider or (metadata.provider if metadata else "") or ""
    kind = classify_provider_error(provider=resolved_provider, error=error)
    detail, guidance = _render_error_copy(
        kind=kind,
        error=error,
        provider=resolved_provider,
    )
    lines = [
        STICKY_MARKER,
        "## 🔎 Lintro Review",
        "",
        f"> ❌ **Review could not complete** — {detail}",
        "",
        guidance,
    ]
    if metadata is not None and metadata.model:
        lines.extend(["", "<sub>" + format_run_mechanics(metadata=metadata) + "</sub>"])
    lines.extend(["", _FOOTER])
    body = "\n".join(lines)
    if prior_runs:
        body += _render_state_block(runs=list(prior_runs))
    return body


def _render_error_copy(
    *,
    kind: ReviewErrorKind,
    error: Exception,
    provider: str,
) -> tuple[str, str]:
    """Build the (detail, guidance) pair for a classified review error.

    The detail is prefixed with the provider label when known and always
    carries the surfaced underlying cause text. For an unknown kind the cause
    text is the primary signal; for known kinds it is appended so the real
    provider message is never lost.

    Args:
        kind: Resolved canonical error kind.
        error: The original exception (possibly a wrapper).
        provider: Provider identifier, used only as a display label.

    Returns:
        Tuple of ``(detail, guidance)`` markdown strings.
    """
    message, guidance = KIND_COPY[kind]
    cause = sanitize_comment_text(resolve_cause_text(error=error), limit=500)
    label = f"`{sanitize_comment_text(provider, limit=40)}` " if provider else ""

    if kind is ReviewErrorKind.UNKNOWN:
        detail = f"{label}{message}: {cause}" if cause else f"{label}{message}"
        return detail, guidance

    detail = f"{label}{message}"
    if cause:
        detail += f" (provider reported: {cause})"
    return detail, guidance
