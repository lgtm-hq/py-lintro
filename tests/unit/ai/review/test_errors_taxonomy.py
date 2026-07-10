"""Tests for the provider-aware review error taxonomy and its sticky copy."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.review.errors_taxonomy import (
    KIND_COPY,
    ReviewErrorKind,
    classify_provider_error,
    resolve_cause_text,
)
from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.github import format_error_comment

# --- per-provider classification --------------------------------------------


def test_anthropic_insufficient_credits() -> None:
    """Anthropic depleted credits (HTTP 400) classify as INSUFFICIENT_CREDITS."""
    error = AIProviderError(
        "Anthropic API error: Error code: 400 - {'type': 'error', 'error': "
        "{'type': 'invalid_request_error', 'message': 'Your credit balance is "
        "too low to access the Anthropic API.'}}",
    )
    kind = classify_provider_error(provider="anthropic", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.INSUFFICIENT_CREDITS)


def test_anthropic_rate_limited() -> None:
    """Anthropic 429 rate_limit_error classifies as RATE_LIMITED."""
    error = AIRateLimitError(
        "Anthropic rate limit exceeded: Error code: 429 - rate_limit_error",
    )
    kind = classify_provider_error(provider="anthropic", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.RATE_LIMITED)


def test_anthropic_auth_failed() -> None:
    """Anthropic 401 authentication_error classifies as AUTH_FAILED."""
    error = AIAuthenticationError(
        "Anthropic authentication failed: Error code: 401 - authentication_error",
    )
    kind = classify_provider_error(provider="anthropic", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.AUTH_FAILED)


def test_anthropic_context_length() -> None:
    """Anthropic 'prompt is too long' classifies as CONTEXT_LENGTH."""
    error = AIProviderError(
        "Anthropic API error: prompt is too long: 250000 tokens > 200000 maximum",
    )
    kind = classify_provider_error(provider="anthropic", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.CONTEXT_LENGTH)


def test_openai_insufficient_credits() -> None:
    """OpenAI 429 insufficient_quota classifies as INSUFFICIENT_CREDITS."""
    error = AIProviderError(
        "OpenAI API error: Error code: 429 - insufficient_quota: You exceeded "
        "your current quota, billing_hard_limit_reached",
    )
    kind = classify_provider_error(provider="openai", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.INSUFFICIENT_CREDITS)


def test_openai_rate_limited_bare_429() -> None:
    """OpenAI rate_limit_exceeded classifies as RATE_LIMITED, not credits."""
    error = AIRateLimitError(
        "OpenAI rate limit: Error code: 429 - rate_limit_exceeded",
    )
    kind = classify_provider_error(provider="openai", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.RATE_LIMITED)


def test_openai_auth_failed() -> None:
    """OpenAI 401 invalid_api_key classifies as AUTH_FAILED."""
    error = AIAuthenticationError(
        "OpenAI authentication failed: Error code: 401 - invalid_api_key",
    )
    kind = classify_provider_error(provider="openai", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.AUTH_FAILED)


def test_cursor_insufficient_credits_stderr() -> None:
    """Cursor CLI stderr credit text classifies as INSUFFICIENT_CREDITS."""
    error = AIProviderError(
        "cursor-agent failed: you are out of credits, top up to continue",
    )
    kind = classify_provider_error(provider="cursor", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.INSUFFICIENT_CREDITS)


def test_cursor_auth_failed_stderr() -> None:
    """Cursor CLI 'not logged in' stderr classifies as AUTH_FAILED."""
    error = AIProviderError("cursor-agent: not logged in, run agent login")
    kind = classify_provider_error(provider="cursor", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.AUTH_FAILED)


def test_cursor_timeout_stderr() -> None:
    """Cursor CLI timeout stderr classifies as TIMEOUT."""
    error = AIProviderError("cursor-agent: request timed out after 600s")
    kind = classify_provider_error(provider="cursor", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.TIMEOUT)


# --- unwrapping and fallbacks -----------------------------------------------


def test_classify_unwraps_review_execution_error() -> None:
    """A ReviewExecutionError wrapper resolves to its underlying cause."""
    cause = AIProviderError(
        "Anthropic API error: Your credit balance is too low",
    )
    wrapper = ReviewExecutionError(
        message="Review aborted before all chunks completed.",
        cause_message=str(cause),
    )
    kind = classify_provider_error(provider="anthropic", error=wrapper)

    assert_that(kind).is_equal_to(ReviewErrorKind.INSUFFICIENT_CREDITS)


def test_classify_prefers_attached_kind() -> None:
    """An error_kind attached by the orchestrator is authoritative."""
    wrapper = ReviewExecutionError(
        message="Review aborted before all chunks completed.",
        cause_message="totally opaque failure",
        error_kind=ReviewErrorKind.SERVER_ERROR,
    )
    kind = classify_provider_error(provider="anthropic", error=wrapper)

    assert_that(kind).is_equal_to(ReviewErrorKind.SERVER_ERROR)


def test_classify_unknown_falls_back() -> None:
    """An unrecognized error falls back to UNKNOWN."""
    error = AIProviderError("something entirely unexpected happened")
    kind = classify_provider_error(provider="anthropic", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.UNKNOWN)


def test_value_error_classifies_as_invalid_response() -> None:
    """A parse ValueError is an invalid-response failure, not a provider error."""
    error = ValueError("could not parse model response as JSON")
    kind = classify_provider_error(provider="anthropic", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.INVALID_RESPONSE)


def test_value_error_with_timeout_text_stays_invalid_response() -> None:
    """A ValueError whose message says "timed out" is still INVALID_RESPONSE."""
    error = ValueError("parsing timed out on malformed model response")
    kind = classify_provider_error(provider="anthropic", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.INVALID_RESPONSE)
    assert_that(kind).is_not_equal_to(ReviewErrorKind.TIMEOUT)


def test_value_error_with_429_text_stays_invalid_response() -> None:
    """A ValueError whose message contains "429" is still INVALID_RESPONSE."""
    error = ValueError("unexpected token near status 429 in model output")
    kind = classify_provider_error(provider="openai", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.INVALID_RESPONSE)
    assert_that(kind).is_not_equal_to(ReviewErrorKind.RATE_LIMITED)


def test_invalid_response_via_wrapped_value_error() -> None:
    """A ValueError chained under the wrapper still resolves to INVALID_RESPONSE."""
    cause = ValueError("malformed model output")
    wrapper = ReviewExecutionError(
        message="Review aborted before all chunks completed.",
    )
    wrapper.__cause__ = cause
    kind = classify_provider_error(provider="anthropic", error=wrapper)

    assert_that(kind).is_equal_to(ReviewErrorKind.INVALID_RESPONSE)


def test_invalid_response_copy_points_at_model_output() -> None:
    """INVALID_RESPONSE copy blames model output, not provider status."""
    message, guidance = KIND_COPY[ReviewErrorKind.INVALID_RESPONSE]

    assert_that(message).contains("malformed")
    assert_that(guidance.lower()).does_not_contain("provider status")


def test_unknown_copy_does_not_assert_provider_error() -> None:
    """UNKNOWN copy stays neutral — it must not claim a provider fault."""
    message, guidance = KIND_COPY[ReviewErrorKind.UNKNOWN]

    assert_that(message).does_not_contain("provider error")
    assert_that(guidance.lower()).does_not_contain("provider status")


def test_resolve_cause_text_prefers_cause_message() -> None:
    """resolve_cause_text surfaces the wrapper's cause_message over the wrapper."""
    wrapper = ReviewExecutionError(
        message="Review aborted before all chunks completed.",
        cause_message="the real provider error",
    )

    assert_that(resolve_cause_text(error=wrapper)).is_equal_to(
        "the real provider error",
    )


def test_shared_fallback_without_provider() -> None:
    """With no provider map, shared heuristics still classify credit errors."""
    error = AIProviderError("insufficient credits remaining on the account")
    kind = classify_provider_error(provider="", error=error)

    assert_that(kind).is_equal_to(ReviewErrorKind.INSUFFICIENT_CREDITS)


# --- sticky copy ------------------------------------------------------------


def test_insufficient_credits_copy_has_guidance() -> None:
    """INSUFFICIENT_CREDITS copy names credits/quota and how to fix."""
    message, guidance = KIND_COPY[ReviewErrorKind.INSUFFICIENT_CREDITS]

    assert_that(message).contains("depleted")
    assert_that(guidance).contains("Top up")
    assert_that(guidance).contains("ai.max_cost_usd")


def test_error_comment_surfaces_real_cause_for_depleted_credits() -> None:
    """The credits sticky surfaces the real cause, not the generic wrapper."""
    cause = AIProviderError(
        "Anthropic API error: Your credit balance is too low",
    )
    wrapper = ReviewExecutionError(
        message="Review aborted before all chunks completed.",
        cause_message=str(cause),
    )
    body = format_error_comment(error=wrapper, provider="anthropic")

    assert_that(body).contains("depleted")
    assert_that(body).contains("Top up")
    assert_that(body).contains("credit balance is too low")
    assert_that(body).does_not_contain("aborted before all chunks")
    assert_that(body).contains("`anthropic`")


def test_error_comment_unknown_includes_surfaced_cause() -> None:
    """An unknown error still surfaces its cause text in the sticky."""
    error = AIProviderError("some brand new failure mode")
    body = format_error_comment(error=error, provider="anthropic")

    assert_that(body).contains("some brand new failure mode")
