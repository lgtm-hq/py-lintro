"""Tests for credential liveness probing.

The behaviour under test is the one #1826 exposed: a *present* credential is not a
*working* one. A depleted Anthropic account authenticates, lists models, and fails
every real call, so these tests pin down that the quota dimension is exercised on
API transport, honestly reported as unverified on CLI transport, and never
collapsed into a silent pass.
"""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.liveness import (
    LIVENESS_PROBE_PROMPT,
    STATE_COPY,
    LivenessResult,
    LivenessState,
    check_liveness_sync,
    incompatible_cli_result,
    live_result,
    liveness_from_error,
    liveness_state_for_kind,
    missing_credential_result,
)
from lintro.ai.providers.base import AIResponse
from lintro.ai.review.errors_taxonomy import ReviewErrorKind
from tests.unit.ai.conftest import MockAIProvider

# --- kind -> state mapping ---------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ReviewErrorKind.AUTH_FAILED, LivenessState.AUTH_FAILED),
        (ReviewErrorKind.INSUFFICIENT_CREDITS, LivenessState.NO_QUOTA),
        (ReviewErrorKind.QUOTA_EXCEEDED, LivenessState.NO_QUOTA),
        (ReviewErrorKind.RATE_LIMITED, LivenessState.RATE_LIMITED),
        (ReviewErrorKind.SERVER_ERROR, LivenessState.UNREACHABLE),
        (ReviewErrorKind.TIMEOUT, LivenessState.UNREACHABLE),
        (ReviewErrorKind.UNKNOWN, LivenessState.UNKNOWN),
    ],
)
def test_error_kinds_map_to_liveness_states(
    *,
    kind: ReviewErrorKind,
    expected: LivenessState,
) -> None:
    """Each canonical provider error kind resolves to one liveness state.

    Args:
        kind: The canonical provider error classification.
        expected: The liveness state it must resolve to.
    """
    assert_that(liveness_state_for_kind(kind=kind)).is_equal_to(expected)


@pytest.mark.parametrize(
    "kind",
    [ReviewErrorKind.CONTEXT_LENGTH, ReviewErrorKind.INVALID_RESPONSE],
)
def test_payload_failures_still_prove_the_credential_is_live(
    *,
    kind: ReviewErrorKind,
) -> None:
    """A provider that answered has a live credential, whatever it answered.

    Args:
        kind: An error kind that can only arise after the provider responded.
    """
    assert_that(liveness_state_for_kind(kind=kind)).is_equal_to(LivenessState.OK)


def test_logged_out_cli_should_classify_as_auth_failed() -> None:
    """A logged-out agent CLI must be an auth verdict, not an unknown one.

    An unauthenticated CLI used to surface as a mysterious failure instead of
    "authenticate the CLI", because the transport built its cause text from
    stderr alone while `claude` reports 'Not logged in · Please run /login' on
    stdout, and Anthropic's signature map carried no auth substrings. Both
    halves now hold, so this asserts the classification directly.
    """
    result = liveness_from_error(
        provider="anthropic",
        transport=AITransport.CLI,
        error=AIProviderError(
            "Claude CLI exited with code 1: Not logged in · Please run /login",
        ),
        quota_verified=False,
    )

    assert_that(result.state).is_equal_to(LivenessState.AUTH_FAILED)
    assert_that(result.is_live).is_false()


def test_every_state_has_copy() -> None:
    """No state may reach a user without a message, so copy must be exhaustive."""
    for state in LivenessState:
        assert_that(STATE_COPY).contains_key(state)
        message, hint = STATE_COPY[state]
        assert_that(message).is_not_empty()
        if state is not LivenessState.OK:
            assert_that(hint).described_as(
                f"{state.value} must tell the operator what to do",
            ).is_not_empty()


# --- result construction -----------------------------------------------------


def test_depleted_balance_is_a_distinct_no_quota_verdict() -> None:
    """The #1826 condition classifies as NO_QUOTA, not as a generic failure."""
    error = AIProviderError(
        "Anthropic API error: Error code: 400 - {'type': 'error', 'error': "
        "{'type': 'invalid_request_error', 'message': 'Your credit balance is "
        "too low to access the Anthropic API.'}}",
    )

    result = liveness_from_error(
        provider="anthropic",
        transport=AITransport.API,
        error=error,
        quota_verified=True,
    )

    assert_that(result.state).is_equal_to(LivenessState.NO_QUOTA)
    assert_that(result.is_live).is_false()
    assert_that(result.quota_verified).is_true()
    assert_that(result.message).contains("credit balance is too low")
    assert_that(result.hint).contains("Top up")


def test_auth_failure_is_not_confused_with_no_quota() -> None:
    """A rejected key and a depleted balance are different verdicts."""
    result = liveness_from_error(
        provider="anthropic",
        transport=AITransport.API,
        error=AIAuthenticationError("Error code: 401 - authentication_error"),
        quota_verified=True,
    )

    assert_that(result.state).is_equal_to(LivenessState.AUTH_FAILED)
    assert_that(result.quota_verified).described_as(
        "an auth rejection happens before quota is consulted",
    ).is_false()


def test_unreachable_provider_does_not_claim_a_quota_verdict() -> None:
    """A 5xx says nothing about quota, so the flag must not be asserted."""
    result = liveness_from_error(
        provider="anthropic",
        transport=AITransport.API,
        error=AIProviderError("Error code: 503 - service unavailable"),
        quota_verified=True,
    )

    assert_that(result.state).is_equal_to(LivenessState.UNREACHABLE)
    assert_that(result.quota_verified).is_false()


def test_rate_limited_credential_is_not_live_but_is_not_broken() -> None:
    """Throttling blocks a call now without implicating the credential."""
    result = liveness_from_error(
        provider="anthropic",
        transport=AITransport.API,
        error=AIRateLimitError("Error code: 429 - rate_limit_error"),
        quota_verified=True,
    )

    assert_that(result.state).is_equal_to(LivenessState.RATE_LIMITED)
    assert_that(result.is_live).is_false()
    assert_that(result.quota_verified).is_false()
    assert_that(result.hint).contains("usable")


def test_live_result_describes_provider_and_transport() -> None:
    """A result renders provider and transport for logs and skip messages."""
    result = live_result(
        provider="anthropic",
        transport=AITransport.API,
        quota_verified=True,
    )

    assert_that(result.is_live).is_true()
    assert_that(result.describe()).is_equal_to("anthropic/api: credential is live")


def test_describe_omits_unknown_transport() -> None:
    """A transport-less result still renders a usable description."""
    result = LivenessResult(
        provider="anthropic",
        transport=None,
        state=LivenessState.UNKNOWN,
        message="probe exploded",
    )

    assert_that(result.describe()).is_equal_to("anthropic: probe exploded")


def test_missing_credential_result_is_not_live() -> None:
    """Absence of a credential is a verdict, not an inconclusive result."""
    result = missing_credential_result(
        provider="openai",
        transport=AITransport.API,
    )

    assert_that(result.state).is_equal_to(LivenessState.MISSING_CREDENTIAL)
    assert_that(result.is_live).is_false()
    assert_that(result.quota_verified).is_false()


def test_incompatible_cli_result_is_cli_scoped() -> None:
    """Flag or version skew is reported against the CLI transport."""
    result = incompatible_cli_result(
        provider="anthropic",
        message="claude CLI no longer advertises required flag(s): --bare",
    )

    assert_that(result.state).is_equal_to(LivenessState.INCOMPATIBLE_CLI)
    assert_that(result.transport).is_equal_to(AITransport.CLI)
    assert_that(result.is_live).is_false()


# --- provider-level probing --------------------------------------------------


async def test_api_probe_makes_a_minimal_real_call() -> None:
    """The API probe must actually invoke the model, one token at a time.

    A presence-only probe cannot see a depleted balance, so this asserts the call
    happens and stays minimal. Driven through the base class rather than a real
    provider: constructing one needs the ``anthropic`` SDK, which the SDK-less CI
    job does not install.
    """
    provider = MockAIProvider()

    result = await provider.check_liveness()

    assert_that(result.is_live).is_true()
    assert_that(result.quota_verified).described_as(
        "a real call is the only thing that can verify quota",
    ).is_true()
    assert_that(provider.calls).is_length(1)
    assert_that(provider.calls[0]["max_tokens"]).is_equal_to(1)
    assert_that(provider.calls[0]["prompt"]).is_equal_to(LIVENESS_PROBE_PROMPT)


async def test_api_probe_reports_depleted_balance(monkeypatch: Any) -> None:
    """A depleted balance surfaces as NO_QUOTA rather than raising.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    provider = MockAIProvider()

    async def _depleted(prompt: str, **kwargs: object) -> AIResponse:
        del prompt, kwargs
        raise AIProviderError(
            "Anthropic API error: Error code: 400 - Your credit balance is too low",
        )

    monkeypatch.setattr(provider, "complete", _depleted)

    result = await provider.check_liveness()

    assert_that(result.state).is_equal_to(LivenessState.NO_QUOTA)
    assert_that(result.is_live).is_false()
    assert_that(result.message).contains("credit balance is too low")


async def test_api_probe_short_circuits_without_a_credential() -> None:
    """No credential means no call: the chain stops at presence."""
    provider = MockAIProvider(available=False)

    result = await provider.check_liveness()

    assert_that(result.state).is_equal_to(LivenessState.MISSING_CREDENTIAL)
    assert_that(provider.calls).described_as(
        "presence must short-circuit before any provider call",
    ).is_empty()
    assert_that(result.message).contains("MOCK_API_KEY")


# --- synchronous entry point -------------------------------------------------


def test_check_liveness_sync_reports_unconstructible_provider(
    monkeypatch: Any,
) -> None:
    """A provider that cannot be built yields a result, not a traceback.

    Doctor must always have something to display; a raised exception there is the
    difference between "your key has no credits" and an unexplained crash.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    def _raise(config: AIConfig) -> None:
        del config
        msg = "AI provider 'anthropic' is recognized but not implemented"
        raise ValueError(msg)

    monkeypatch.setattr("lintro.ai.providers.get_provider", _raise)

    result = check_liveness_sync(
        config=AIConfig(enabled=True, transport=AITransport.API),
    )

    assert_that(result.state).is_equal_to(LivenessState.MISSING_CREDENTIAL)
    assert_that(result.message).contains("could not be constructed")


def test_check_liveness_sync_runs_the_provider_probe(monkeypatch: Any) -> None:
    """The sync entry point drives the async provider probe to completion.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    expected = live_result(
        provider="anthropic",
        transport=AITransport.API,
        quota_verified=True,
    )

    class _Stub:
        async def check_liveness(self, *, timeout: float) -> LivenessResult:
            """Return a canned verdict.

            Args:
                timeout: Ignored.

            Returns:
                The canned liveness result.
            """
            del timeout
            return expected

    monkeypatch.setattr(
        "lintro.ai.providers.get_provider",
        lambda config: _Stub(),
    )

    result = check_liveness_sync(
        config=AIConfig(enabled=True, transport=AITransport.API),
    )

    assert_that(result).is_equal_to(expected)


def test_check_liveness_sync_surfaces_missing_sdk(monkeypatch: Any) -> None:
    """A missing provider SDK is a liveness verdict, not an exception.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    def _raise(config: AIConfig) -> None:
        del config
        raise AINotAvailableError("provider requires the 'anthropic' package")

    monkeypatch.setattr("lintro.ai.providers.get_provider", _raise)

    result = check_liveness_sync(
        config=AIConfig(enabled=True, transport=AITransport.API),
    )

    assert_that(result.state).is_equal_to(LivenessState.MISSING_CREDENTIAL)
    assert_that(result.hint).contains("lintro[ai]")
