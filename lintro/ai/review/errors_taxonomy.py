"""Provider-aware error taxonomy for AI review failures.

Classifies a raw provider or orchestrator exception into a canonical
:class:`ReviewErrorKind` so the PR error sticky can surface the *real* cause
(for example depleted API credits) instead of a generic "review aborted"
message. Each provider signals the same canonical condition differently
(Anthropic returns HTTP 400 for depleted credits, OpenAI returns HTTP 429 with
``insufficient_quota``, CLI transports only emit stderr text), so the mapping is
expressed as a per-provider signature map layered over a shared fallback set.

Adding a new provider is a single entry in :data:`PROVIDER_ERROR_SIGNATURES`;
the canonical kinds and their user-facing copy in :data:`KIND_COPY` never
change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, auto

from lintro.ai.exceptions import AIAuthenticationError, AIRateLimitError


class ReviewErrorKind(StrEnum):
    """Canonical, provider-agnostic classification of a review failure.

    Members:
        AUTH_FAILED: The provider rejected the API key (invalid or missing).
        INSUFFICIENT_CREDITS: The account balance/credits are depleted or a hard
            billing limit was hit (distinct from a plan quota window).
        QUOTA_EXCEEDED: A plan or usage quota was exceeded (distinct from
            pay-as-you-go credit depletion).
        RATE_LIMITED: The provider throttled the request (HTTP 429).
        CONTEXT_LENGTH: The prompt exceeded the model's maximum context window.
        SERVER_ERROR: The provider returned a 5xx / overloaded response.
        TIMEOUT: The request timed out before a response was returned.
        INVALID_RESPONSE: The model returned a malformed/unparseable response
            (a lintro-side parse/validation failure, not a provider transport
            error).
        UNKNOWN: No signature matched; the surfaced cause text is shown as-is.
    """

    AUTH_FAILED = auto()
    INSUFFICIENT_CREDITS = auto()
    QUOTA_EXCEEDED = auto()
    RATE_LIMITED = auto()
    CONTEXT_LENGTH = auto()
    SERVER_ERROR = auto()
    TIMEOUT = auto()
    INVALID_RESPONSE = auto()
    UNKNOWN = auto()


@dataclass(frozen=True, slots=True)
class ErrorMatcher:
    """A single (status codes, substrings) signature for one error kind.

    A matcher fires when the error text contains any of its case-insensitive
    substrings, or when an extracted HTTP status matches one of its status
    codes. Substrings are the primary discriminator (CLI transports have no
    status); status codes corroborate or catch bare-status errors.

    Attributes:
        substrings: Lowercase needles matched against the lowercased error text.
        statuses: HTTP status codes that also indicate this kind.
    """

    substrings: tuple[str, ...] = ()
    statuses: tuple[int, ...] = ()

    def matches(self, *, status: int | None, text: str) -> bool:
        """Return whether this matcher fires for the given status and text.

        Args:
            status: Extracted HTTP status code, or ``None`` when unavailable.
            text: Lowercased error text to search.

        Returns:
            True when any substring is present or the status is listed.
        """
        text_ok = any(needle in text for needle in self.substrings)
        status_ok = status is not None and status in self.statuses
        return text_ok or status_ok


# Order in which kinds are tested. More specific/discriminating kinds are
# checked before broader ones so, e.g., an OpenAI ``insufficient_quota`` on a
# 429 resolves to INSUFFICIENT_CREDITS rather than a bare RATE_LIMITED.
_KIND_PRIORITY: tuple[ReviewErrorKind, ...] = (
    ReviewErrorKind.INSUFFICIENT_CREDITS,
    ReviewErrorKind.QUOTA_EXCEEDED,
    ReviewErrorKind.AUTH_FAILED,
    ReviewErrorKind.CONTEXT_LENGTH,
    ReviewErrorKind.RATE_LIMITED,
    ReviewErrorKind.TIMEOUT,
    ReviewErrorKind.SERVER_ERROR,
)


PROVIDER_ERROR_SIGNATURES: dict[str, dict[ReviewErrorKind, ErrorMatcher]] = {
    "anthropic": {
        # Depleted credits: HTTP 400 invalid_request_error, NOT 402/429.
        ReviewErrorKind.INSUFFICIENT_CREDITS: ErrorMatcher(
            substrings=("credit balance is too low", "credit balance"),
        ),
        ReviewErrorKind.RATE_LIMITED: ErrorMatcher(
            substrings=("rate_limit_error", "rate limit"),
            statuses=(429,),
        ),
        ReviewErrorKind.AUTH_FAILED: ErrorMatcher(
            substrings=("authentication_error", "invalid x-api-key", "invalid api key"),
            statuses=(401,),
        ),
        ReviewErrorKind.CONTEXT_LENGTH: ErrorMatcher(
            substrings=("prompt is too long", "maximum context"),
        ),
        ReviewErrorKind.SERVER_ERROR: ErrorMatcher(
            substrings=("overloaded_error", "overloaded", "api_error"),
            statuses=(500, 502, 503, 504, 529),
        ),
    },
    "openai": {
        # Depleted credits / hard billing limit: 429 with insufficient_quota.
        ReviewErrorKind.INSUFFICIENT_CREDITS: ErrorMatcher(
            substrings=("insufficient_quota", "billing_hard_limit_reached"),
        ),
        ReviewErrorKind.RATE_LIMITED: ErrorMatcher(
            substrings=("rate_limit_exceeded", "rate limit"),
            statuses=(429,),
        ),
        ReviewErrorKind.AUTH_FAILED: ErrorMatcher(
            substrings=("invalid_api_key", "invalid api key"),
            statuses=(401,),
        ),
        ReviewErrorKind.CONTEXT_LENGTH: ErrorMatcher(
            substrings=("context_length_exceeded", "maximum context length"),
        ),
        ReviewErrorKind.SERVER_ERROR: ErrorMatcher(
            substrings=("server_error", "service unavailable"),
            statuses=(500, 502, 503, 504),
        ),
    },
    "cursor": {
        # CLI transport: no HTTP status, only stderr text.
        ReviewErrorKind.INSUFFICIENT_CREDITS: ErrorMatcher(
            substrings=(
                "credit balance",
                "insufficient credits",
                "out of credits",
                "usage limit reached",
            ),
        ),
        ReviewErrorKind.RATE_LIMITED: ErrorMatcher(
            substrings=("rate limit", "too many requests"),
        ),
        ReviewErrorKind.AUTH_FAILED: ErrorMatcher(
            substrings=(
                "not logged in",
                "authentication",
                "unauthorized",
                "invalid api key",
                "agent login",
            ),
        ),
        ReviewErrorKind.CONTEXT_LENGTH: ErrorMatcher(
            substrings=("context length", "too long", "maximum context"),
        ),
        ReviewErrorKind.TIMEOUT: ErrorMatcher(
            substrings=("timed out", "timeout"),
        ),
    },
}


# Shared heuristics applied when no provider-specific signature matches. Kept
# deliberately broad so a novel provider still resolves to a sensible kind.
_SHARED_SIGNATURES: dict[ReviewErrorKind, ErrorMatcher] = {
    ReviewErrorKind.INSUFFICIENT_CREDITS: ErrorMatcher(
        substrings=(
            "credit balance",
            "insufficient_quota",
            "insufficient credits",
            "out of credits",
            "billing_hard_limit_reached",
            "billing hard limit",
            "payment required",
        ),
        statuses=(402,),
    ),
    ReviewErrorKind.QUOTA_EXCEEDED: ErrorMatcher(
        substrings=("quota exceeded", "exceeded your current quota", "usage limit"),
    ),
    ReviewErrorKind.AUTH_FAILED: ErrorMatcher(
        substrings=(
            "authentication",
            "unauthorized",
            "invalid api key",
            "invalid_api_key",
        ),
        statuses=(401,),
    ),
    ReviewErrorKind.CONTEXT_LENGTH: ErrorMatcher(
        substrings=(
            "context length",
            "maximum context",
            "prompt is too long",
            "context_length_exceeded",
            "too many tokens",
        ),
    ),
    ReviewErrorKind.RATE_LIMITED: ErrorMatcher(
        substrings=("rate limit", "rate_limit", "too many requests"),
        statuses=(429,),
    ),
    ReviewErrorKind.TIMEOUT: ErrorMatcher(
        substrings=("timed out", "timeout"),
    ),
    ReviewErrorKind.SERVER_ERROR: ErrorMatcher(
        substrings=(
            "server error",
            "internal server error",
            "service unavailable",
            "overloaded",
            "bad gateway",
        ),
        statuses=(500, 502, 503, 504),
    ),
}


# Provider-agnostic (message, guidance) copy for the PR error sticky.
KIND_COPY: dict[ReviewErrorKind, tuple[str, str]] = {
    ReviewErrorKind.AUTH_FAILED: (
        "authentication failed (invalid or missing API key)",
        "Check the provider API key configured for this workflow (e.g. the "
        "`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` secret).",
    ),
    ReviewErrorKind.INSUFFICIENT_CREDITS: (
        "the provider reported no available quota or credits — the account "
        "balance is depleted",
        "Top up the provider account (or raise the plan quota), or lower "
        "`ai.max_cost_usd`, then re-run.",
    ),
    ReviewErrorKind.QUOTA_EXCEEDED: (
        "the provider reported the plan quota was exceeded",
        "Raise the plan quota/limit or wait for the quota window to reset, then "
        "re-run.",
    ),
    ReviewErrorKind.RATE_LIMITED: (
        "the provider rate-limited the request (429)",
        "Retry later, lower review depth, or switch provider/model.",
    ),
    ReviewErrorKind.CONTEXT_LENGTH: (
        "the request exceeded the model's maximum context length",
        "Narrow `--path` to a smaller diff, lower review depth, or use a model "
        "with a larger context window.",
    ),
    ReviewErrorKind.SERVER_ERROR: (
        "the provider returned a server error (5xx)",
        "This is usually transient — retry shortly.",
    ),
    ReviewErrorKind.TIMEOUT: (
        "the request timed out",
        "Retry, raise `ai.api_timeout`, or narrow `--path` to a smaller diff.",
    ),
    ReviewErrorKind.INVALID_RESPONSE: (
        "the model returned a malformed or unparseable response",
        "Retry the review — model output may have been malformed — or try a "
        "different model via `ai.model`.",
    ),
    ReviewErrorKind.UNKNOWN: (
        "the review could not be completed",
        "See the cause above and the workflow logs; retry if it looks transient.",
    ),
}


_STATUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"error code[:\s]+(\d{3})"),
    re.compile(r"status[_ ]?code[:\s=]+(\d{3})"),
    re.compile(r"http[/ ]?(?:status[: ]*)?(\d{3})"),
    re.compile(r"^\s*(\d{3})\b"),
)


def _extract_status(*, text: str) -> int | None:
    """Extract an HTTP status code from error text when clearly present.

    Args:
        text: Lowercased error text.

    Returns:
        The parsed status code, or ``None`` when no confident match is found.
    """
    for pattern in _STATUS_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return int(match.group(1))
    return None


def resolve_cause_text(*, error: Exception) -> str:
    """Resolve the most specific underlying cause message for an error.

    Unwraps a :class:`~lintro.ai.review.exceptions.ReviewExecutionError` to its
    captured ``cause_message`` (the real provider error), falling back to the
    chained ``__cause__`` and finally the exception's own string form.

    Args:
        error: The exception raised during review.

    Returns:
        The most informative human-readable cause text available.
    """
    from lintro.ai.review.exceptions import ReviewExecutionError

    if isinstance(error, ReviewExecutionError) and error.cause_message:
        return error.cause_message
    cause = error.__cause__
    if isinstance(cause, BaseException):
        return str(cause)
    return str(error)


def _resolve_cause_exception(*, error: Exception) -> BaseException:
    """Return the deepest chained cause exception (for ``isinstance`` checks)."""
    current: BaseException = error
    while isinstance(current.__cause__, BaseException):
        current = current.__cause__
    return current


def classify_provider_error(*, provider: str, error: Exception) -> ReviewErrorKind:
    """Classify a review error into a canonical :class:`ReviewErrorKind`.

    Resolves the underlying provider cause (unwrapping any
    ``ReviewExecutionError`` wrapper), then tests it against the provider's
    signature map, the lintro AI exception hierarchy, and finally a shared
    fallback set before defaulting to :attr:`ReviewErrorKind.UNKNOWN`. When the
    error already carries a resolved kind (attached by the orchestrator), that
    kind is authoritative.

    Args:
        provider: Provider identifier (e.g. ``"anthropic"``); case-insensitive.
        error: The exception raised during review, possibly a wrapper.

    Returns:
        The resolved canonical error kind.
    """
    from lintro.ai.review.exceptions import ReviewExecutionError

    if isinstance(error, ReviewExecutionError) and error.error_kind is not None:
        return error.error_kind

    text = resolve_cause_text(error=error).lower()
    status = _extract_status(text=text)
    cause_exc = _resolve_cause_exception(error=error)

    signatures = PROVIDER_ERROR_SIGNATURES.get((provider or "").lower(), {})
    for kind in _KIND_PRIORITY:
        matcher = signatures.get(kind)
        if matcher is not None and matcher.matches(status=status, text=text):
            return kind

    # Subsume the existing lintro AI exception hierarchy: a typed auth/rate-limit
    # error is authoritative even when its text carries no matching substring.
    if isinstance(cause_exc, AIAuthenticationError):
        return ReviewErrorKind.AUTH_FAILED
    if isinstance(cause_exc, AIRateLimitError):
        return ReviewErrorKind.RATE_LIMITED

    for kind in _KIND_PRIORITY:
        matcher = _SHARED_SIGNATURES.get(kind)
        if matcher is not None and matcher.matches(status=status, text=text):
            return kind

    # A bare ``ValueError`` cause is a lintro-side parse/validation failure of
    # the model response, not a provider transport error — surface it as such
    # rather than misdirecting the user to check provider status.
    if isinstance(cause_exc, ValueError):
        return ReviewErrorKind.INVALID_RESPONSE

    return ReviewErrorKind.UNKNOWN
