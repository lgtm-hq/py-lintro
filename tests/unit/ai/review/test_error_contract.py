"""Tests for the machine-readable JSON error contract for review failures."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.review.error_contract import (
    RETRYABLE_KINDS,
    REVIEW_ERROR_EXIT_CODE,
    build_error_contract,
    is_retryable_kind,
    render_error_contract_json,
)
from lintro.ai.review.errors_taxonomy import ReviewErrorKind
from lintro.ai.review.exceptions import ReviewExecutionError


@pytest.fixture
def auth_error() -> AIAuthenticationError:
    """Return an Anthropic 401 authentication failure."""
    return AIAuthenticationError(
        "Anthropic authentication failed: Error code: 401 - authentication_error",
    )


@pytest.fixture
def rate_limit_error() -> AIRateLimitError:
    """Return an Anthropic 429 rate-limit failure."""
    return AIRateLimitError(
        "Anthropic rate limit exceeded: Error code: 429 - rate_limit_error",
    )


@pytest.fixture
def quota_error() -> AIProviderError:
    """Return an OpenAI depleted-quota failure (429 insufficient_quota)."""
    return AIProviderError(
        "OpenAI API error: Error code: 429 - {'error': {'code': "
        "'insufficient_quota', 'message': 'You exceeded your current quota.'}}",
    )


@pytest.fixture
def server_error() -> AIProviderError:
    """Return an Anthropic 5xx overloaded failure."""
    return AIProviderError(
        "Anthropic API error: Error code: 529 - overloaded_error",
    )


@pytest.fixture
def invalid_response_error() -> ReviewExecutionError:
    """Return a lintro-side malformed-response parse failure."""
    return ReviewExecutionError(
        message="review failed",
        cause_message="Expecting value: line 1 column 1 (char 0)",
        error_kind=ReviewErrorKind.INVALID_RESPONSE,
    )


# --- exact envelope shape per representative failure ------------------------


def test_auth_401_envelope_shape(auth_error: AIAuthenticationError) -> None:
    """A 401 auth failure yields a non-retryable auth_failed envelope."""
    contract = build_error_contract(provider="anthropic", error=auth_error)

    assert_that(contract).is_equal_to(
        {
            "error": {
                "kind": "auth_failed",
                "provider": "anthropic",
                "status": 401,
                "retryable": False,
                "message": (
                    "Anthropic authentication failed: Error code: 401 - "
                    "authentication_error"
                ),
            },
        },
    )


def test_rate_limit_429_envelope_shape(rate_limit_error: AIRateLimitError) -> None:
    """A 429 rate limit yields a retryable rate_limited envelope."""
    contract = build_error_contract(provider="anthropic", error=rate_limit_error)

    assert_that(contract).is_equal_to(
        {
            "error": {
                "kind": "rate_limited",
                "provider": "anthropic",
                "status": 429,
                "retryable": True,
                "message": (
                    "Anthropic rate limit exceeded: Error code: 429 - "
                    "rate_limit_error"
                ),
            },
        },
    )


def test_quota_429_envelope_shape(quota_error: AIProviderError) -> None:
    """OpenAI insufficient_quota classifies as non-retryable insufficient_credits."""
    contract = build_error_contract(provider="openai", error=quota_error)
    error = contract["error"]

    assert_that(error["kind"]).is_equal_to("insufficient_credits")
    assert_that(error["provider"]).is_equal_to("openai")
    assert_that(error["status"]).is_equal_to(429)
    assert_that(error["retryable"]).is_false()
    assert_that(error["message"]).contains("insufficient_quota")


def test_server_5xx_envelope_shape(server_error: AIProviderError) -> None:
    """A 5xx overloaded response yields a retryable server_error envelope."""
    contract = build_error_contract(provider="anthropic", error=server_error)

    assert_that(contract).is_equal_to(
        {
            "error": {
                "kind": "server_error",
                "provider": "anthropic",
                "status": 529,
                "retryable": True,
                "message": "Anthropic API error: Error code: 529 - overloaded_error",
            },
        },
    )


def test_invalid_response_envelope_shape(
    invalid_response_error: ReviewExecutionError,
) -> None:
    """A malformed model response yields a non-retryable invalid_response envelope."""
    contract = build_error_contract(provider="anthropic", error=invalid_response_error)

    assert_that(contract).is_equal_to(
        {
            "error": {
                "kind": "invalid_response",
                "provider": "anthropic",
                "status": None,
                "retryable": False,
                "message": "Expecting value: line 1 column 1 (char 0)",
            },
        },
    )


# --- schema invariants ------------------------------------------------------


def test_status_is_null_when_absent() -> None:
    """A CLI-transport error with no HTTP status reports a null status."""
    error = AIProviderError("cursor-sdk: agent not logged in")
    contract = build_error_contract(provider="cursor", error=error)

    assert_that(contract["error"]["status"]).is_none()
    assert_that(contract["error"]["kind"]).is_equal_to("auth_failed")


def test_provider_is_lowercased() -> None:
    """The provider field is normalized to lowercase."""
    error = AIProviderError("Error code: 500 - internal server error")
    contract = build_error_contract(provider="Anthropic", error=error)

    assert_that(contract["error"]["provider"]).is_equal_to("anthropic")


def test_envelope_keys_are_stable(auth_error: AIAuthenticationError) -> None:
    """The envelope always carries exactly the documented key set."""
    contract = build_error_contract(provider="anthropic", error=auth_error)

    assert_that(list(contract.keys())).is_equal_to(["error"])
    assert_that(set(contract["error"].keys())).is_equal_to(
        {"kind", "provider", "status", "retryable", "message"},
    )


def test_retryable_kinds_membership() -> None:
    """Only transient transport kinds are marked retryable."""
    assert_that(is_retryable_kind(kind=ReviewErrorKind.RATE_LIMITED)).is_true()
    assert_that(is_retryable_kind(kind=ReviewErrorKind.SERVER_ERROR)).is_true()
    assert_that(is_retryable_kind(kind=ReviewErrorKind.TIMEOUT)).is_true()
    assert_that(is_retryable_kind(kind=ReviewErrorKind.AUTH_FAILED)).is_false()
    assert_that(is_retryable_kind(kind=ReviewErrorKind.INVALID_RESPONSE)).is_false()
    assert_that(RETRYABLE_KINDS).contains(ReviewErrorKind.RATE_LIMITED)


def test_render_json_is_parseable_and_indented(
    rate_limit_error: AIRateLimitError,
) -> None:
    """The rendered contract is valid two-space-indented JSON."""
    rendered = render_error_contract_json(
        provider="anthropic",
        error=rate_limit_error,
    )
    parsed = json.loads(rendered)

    assert_that(parsed["error"]["kind"]).is_equal_to("rate_limited")
    assert_that(rendered).contains('\n  "error"')


def test_error_exit_code_is_distinct_from_findings() -> None:
    """The provider-error exit code differs from the P1-findings exit code (1)."""
    assert_that(REVIEW_ERROR_EXIT_CODE).is_equal_to(2)
    assert_that(REVIEW_ERROR_EXIT_CODE).is_not_equal_to(1)
