"""Error sticky-comment rendering for GitHub AI reviews."""

from __future__ import annotations

import re
from typing import Any

from lintro.ai.review.errors_taxonomy import (
    KIND_COPY,
    ReviewErrorKind,
    classify_provider_error,
    resolve_cause_text,
)
from lintro.ai.review.github_constants import _FOOTER, MAX_COMMENT_CHARS, STICKY_MARKER
from lintro.ai.review.github_render import format_run_mechanics, sanitize_comment_text
from lintro.ai.review.github_sticky import render_state_sticky
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.review_state_codec import (
    prune_state_to_fit,
    render_state_block,
    renumber_if_legacy_v1,
)

#: Cap on the provider error text rendered on the error-only sticky. Provider
#: failures can carry a whole CLI JSON payload as their message, and none of it
#: past the first sentences helps a reader decide whether to retry.
ERROR_CAUSE_LIMIT = 500
#: Tighter cap for the one-line failure banner prepended to a re-rendered
#: board: it shares a blockquote with the "showing round M results" pointer, so
#: it has to stay scannable at a glance.
BANNER_CAUSE_LIMIT = 200

#: Headline of the error-only surface, shown when no prior round can be
#: re-rendered. Named so callers and tests can key on the wording without
#: transcribing it.
ERROR_ONLY_HEADLINE = "❌ **Review could not complete**"

#: Headline of the failure banner shown over a re-rendered board, formatted
#: with the round number that failed.
FAILURE_BANNER_HEADLINE = "⚠️ **Round {round_number} could not complete**"

_WHITESPACE_RE = re.compile(r"\s+")


def format_error_comment(
    *,
    error: Exception,
    provider: str | None = None,
    metadata: ReviewMetadata | None = None,
    prior_runs: list[dict[str, Any]] | None = None,
    prior_state: ReviewState | None = None,
    repo: str = "",
    pr_number: int | None = None,
) -> str:
    """Format a provider/API error as a clear PR comment.

    Classifies the error provider-aware, against the underlying provider cause
    (unwrapping any orchestrator wrapper), into a canonical
    :class:`~lintro.ai.review.errors_taxonomy.ReviewErrorKind` and renders the
    specific sticky for that kind — for example, depleted credits render as a
    clear "top up or lower ai.max_cost_usd" message rather than a generic
    "review aborted". The real underlying cause text is always surfaced.

    When the persisted state already carries a successful run, the failure does
    *not* replace the board (#1954): the mission-control layout is re-rendered
    from that state with a one-line banner under the header explaining which
    round failed and which round is on screen. A transient provider blip is a
    footnote on the dashboard, not a reason to erase it. The error-only surface
    is reserved for a first-round failure, where there is nothing to show.

    Args:
        error: The exception raised during review.
        provider: Provider identifier used for provider-aware classification.
            Falls back to ``metadata.provider`` when omitted.
        metadata: Optional review metadata for a mechanics footer.
        prior_runs: Legacy run mappings recovered from the previous sticky
            comment. Ignored when ``prior_state`` is given.
        prior_state: Full state decoded from the previous sticky comment.
            Re-emitted verbatim so a transient error does not reset cumulative
            telemetry, advance the round counter, or touch the tracked
            finding history.
        repo: ``owner/name`` slug used to link finding titles to their threads
            on a re-rendered board.
        pr_number: Pull request number used for the same links.

    Returns:
        Markdown comment body: the last good board with a failure banner, or
        the error-only surface describing the failure and next steps.
    """
    resolved_provider = provider or (metadata.provider if metadata else "") or ""
    kind = classify_provider_error(provider=resolved_provider, error=error)
    state = _resolve_prior_state(prior_runs=prior_runs, prior_state=prior_state)

    if state is not None and state.runs:
        return render_state_sticky(
            state=state,
            banner=_failure_banner(
                kind=kind,
                error=error,
                provider=resolved_provider,
                state=state,
            ),
            repo=repo,
            pr_number=pr_number,
        )

    detail, guidance = _render_error_copy(
        kind=kind,
        error=error,
        provider=resolved_provider,
        cause_limit=ERROR_CAUSE_LIMIT,
    )
    lines = [
        STICKY_MARKER,
        "## 🔎 Lintro Review",
        "",
        f"> {ERROR_ONLY_HEADLINE} — {detail}",
        "",
        guidance,
    ]
    if metadata is not None and metadata.model:
        lines.extend(["", "<sub>" + format_run_mechanics(metadata=metadata) + "</sub>"])
    lines.extend(["", _FOOTER])
    body = "\n".join(lines)
    if state is not None and (state.findings or state.truncated):
        block = render_state_block(
            state=prune_state_to_fit(
                state=state,
                body=body,
                limit=MAX_COMMENT_CHARS,
            ),
        )
        if len(body) + len(block) > MAX_COMMENT_CHARS:
            # Same floor-overflow last resort as the sticky path: keep an
            # authentic (empty) block so a forged marker in error prose can
            # never become the last well-formed candidate.
            block = render_state_block(
                state=ReviewState(runs=(), findings=(), truncated=True),
            )
        body += block
    return body


def _resolve_prior_state(
    *,
    prior_runs: list[dict[str, Any]] | None,
    prior_state: ReviewState | None,
) -> ReviewState | None:
    """Return the state to re-emit, upgrading legacy run mappings when needed.

    Args:
        prior_runs: Legacy run mappings recovered from the previous sticky
            comment. Ignored when ``prior_state`` is given.
        prior_state: Full state decoded from the previous sticky comment.

    Returns:
        The resolved state, or ``None`` when nothing was carried over.
    """
    if prior_state is not None:
        return prior_state
    if not prior_runs:
        return None
    runs = tuple(RunRecord.from_dict(run) for run in prior_runs)
    return ReviewState(runs=renumber_if_legacy_v1(runs=runs))


def _failure_banner(
    *,
    kind: ReviewErrorKind,
    error: Exception,
    provider: str,
    state: ReviewState,
) -> str:
    """Render the blockquote explaining a failed round over the last good board.

    The round it names is the one the *next successful* review will be numbered
    with, because that is the only round number that exists: a round number is
    assigned when a review completes, and a failed attempt deliberately writes
    nothing to state. Consecutive failures therefore repeat the same number,
    which is correct — they are repeated attempts at the same round, and the
    sticky is edited in place so only the latest attempt is ever on screen.
    Counting attempts instead would mean recording failures in the state blob,
    which is exactly what a failed round must not do (#1954).

    The closing sentence is the same kind-specific guidance the error-only
    surface renders, not a fixed "retry shortly": a depleted balance or a
    rejected API key will fail identically on every retry, and telling a
    reviewer to try again is worse than saying nothing.

    Args:
        kind: Resolved canonical error kind.
        error: The exception raised during review.
        provider: Provider identifier, used only as a display label.
        state: Persisted state whose board is being re-rendered.

    Returns:
        A single-line Markdown blockquote naming the failed round, the cause,
        the round actually on screen, and what to do about it.
    """
    detail, guidance = _render_error_copy(
        kind=kind,
        error=error,
        provider=provider,
        cause_limit=BANNER_CAUSE_LIMIT,
    )
    shown = max(run.round for run in state.runs)
    headline = FAILURE_BANNER_HEADLINE.format(round_number=state.next_round)
    return (
        f"> {headline} — {detail} · showing round {shown} results below. " f"{guidance}"
    )


def _render_error_copy(
    *,
    kind: ReviewErrorKind,
    error: Exception,
    provider: str,
    cause_limit: int = ERROR_CAUSE_LIMIT,
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
        cause_limit: Maximum length of the surfaced cause text.

    Returns:
        Tuple of ``(detail, guidance)`` markdown strings.
    """
    message, guidance = KIND_COPY[kind]
    cause = condense_provider_error(
        text=resolve_cause_text(error=error),
        limit=cause_limit,
    )
    label = f"`{sanitize_comment_text(provider, limit=40)}` " if provider else ""

    if kind is ReviewErrorKind.UNKNOWN:
        detail = f"{label}{message}: {cause}" if cause else f"{label}{message}"
        return detail, guidance

    detail = f"{label}{message}"
    if cause:
        detail += f" (provider reported: {cause})"
    return detail, guidance


def condense_provider_error(*, text: str, limit: int) -> str:
    """Flatten and bound provider error text for rendering inside a blockquote.

    Provider failures routinely carry a multi-line CLI JSON payload as their
    message. Rendered raw, the newlines break out of the blockquote the detail
    lives in and the payload itself can run for thousands of characters,
    crowding out the state block the sticky needs room for. Collapsing the
    whitespace keeps the detail on one line; the limit keeps it readable.

    Args:
        text: Raw provider error text.
        limit: Maximum length of the returned string.

    Returns:
        Sanitized single-line text, truncated with an ellipsis when over
        ``limit``.
    """
    flattened = _WHITESPACE_RE.sub(" ", text or "").strip()
    return sanitize_comment_text(flattened, limit=limit)
