"""Machine-readable JSON error contract for AI review provider failures.

When ``lintro review --output json`` cannot complete because the provider
failed (invalid key, rate limit, depleted quota, 5xx, malformed response), a
success envelope (``metadata``/``summary``/``checklist``/``findings``) can never
be produced. This module builds a *stable* error envelope instead so CI
consumers can classify the failure on ``error.kind``/``error.status`` rather
than scraping human-readable stderr prose.

The envelope shape is::

    {
      "error": {
        "kind": "<ReviewErrorKind value>",   # e.g. "auth_failed", "rate_limited"
        "provider": "anthropic",             # provider identifier, lowercased
        "status": 401,                        # extracted HTTP status, or null
        "retryable": false,                   # safe to retry unchanged?
        "message": "..."                      # most specific cause text
      }
    }

``error.kind`` reuses the canonical :class:`ReviewErrorKind` taxonomy, so the
set of values is the single source of truth shared with the PR error sticky.
The envelope is emitted on stdout; the review command exits with
:data:`REVIEW_ERROR_EXIT_CODE` so a provider error is distinguishable from the
exit code ``1`` that signals *P1 findings present* on a successful review.
"""

from __future__ import annotations

from typing import Any, Final

from lintro.ai.review.errors_taxonomy import (
    ReviewErrorKind,
    _extract_status,
    classify_provider_error,
    resolve_cause_text,
)

__all__ = [
    "REVIEW_ERROR_EXIT_CODE",
    "RETRYABLE_KINDS",
    "build_error_contract",
    "is_retryable_kind",
    "render_error_contract_json",
]

# Distinct exit code for a provider/execution failure under ``--output json``.
# Exit ``1`` stays reserved for a *successful* review that found P1 issues, so
# consumers never have to disambiguate "findings" from "error" by exit code.
REVIEW_ERROR_EXIT_CODE: Final[int] = 2

# Kinds that are safe to retry unchanged (transient transport conditions).
# Everything else needs operator action (fix the key, top up credits, shrink
# the diff) or is a lintro-side parse failure, so retrying as-is will not help.
RETRYABLE_KINDS: Final[frozenset[ReviewErrorKind]] = frozenset(
    {
        ReviewErrorKind.RATE_LIMITED,
        ReviewErrorKind.SERVER_ERROR,
        ReviewErrorKind.TIMEOUT,
    },
)


def is_retryable_kind(*, kind: ReviewErrorKind) -> bool:
    """Return whether an error kind is safe to retry without changes.

    Args:
        kind: The canonical error classification.

    Returns:
        True for transient transport conditions (rate limit, 5xx, timeout).
    """
    return kind in RETRYABLE_KINDS


def build_error_contract(*, provider: str, error: Exception) -> dict[str, Any]:
    """Build the machine-readable error envelope for a review failure.

    Args:
        provider: Provider identifier (e.g. ``"anthropic"``); case-insensitive.
        error: The exception that aborted the review, possibly a wrapper.

    Returns:
        A JSON-serializable ``{"error": {...}}`` envelope with a canonical
        ``kind``, the ``provider``, the extracted HTTP ``status`` (or ``None``),
        a ``retryable`` flag, and the most specific cause ``message``.
    """
    kind = classify_provider_error(provider=provider, error=error)
    message = resolve_cause_text(error=error)
    status = _extract_status(text=message.lower())
    return {
        "error": {
            "kind": kind.value,
            "provider": (provider or "").lower(),
            "status": status,
            "retryable": is_retryable_kind(kind=kind),
            "message": message,
        },
    }


def render_error_contract_json(*, provider: str, error: Exception) -> str:
    """Render the review error envelope as pretty-printed JSON text.

    Args:
        provider: Provider identifier (e.g. ``"anthropic"``); case-insensitive.
        error: The exception that aborted the review.

    Returns:
        JSON string with two-space indentation matching the success envelope.
    """
    import json

    return json.dumps(build_error_contract(provider=provider, error=error), indent=2)
