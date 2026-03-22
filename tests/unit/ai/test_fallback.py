"""Tests for the runtime model fallback chain."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.fallback import complete_with_fallback
from lintro.ai.providers.base import AIResponse


def _make_provider(model: str = "primary-model") -> MagicMock:
    """Create a mock provider with a configurable model_name attribute."""
    provider = MagicMock()
    provider.model_name = model
    return provider


def _ok_response(model: str = "primary-model") -> AIResponse:
    """Create a successful mock AI response."""
    return AIResponse(
        content="ok",
        model=model,
        input_tokens=10,
        output_tokens=5,
        cost_estimate=0.001,
        provider="mock",
    )


# -- TestCompleteWithFallbackPrimarySuccess: Primary model succeeds on first try.


def test_returns_response_without_fallback() -> None:
    """Return response when primary succeeds without fallback."""
    provider = _make_provider()
    provider.complete.return_value = _ok_response()

    result = complete_with_fallback(provider, "hello")

    assert_that(result.content).is_equal_to("ok")
    provider.complete.assert_called_once()


def test_returns_response_with_empty_fallback_list() -> None:
    """Return response when fallback list is empty."""
    provider = _make_provider()
    provider.complete.return_value = _ok_response()

    result = complete_with_fallback(provider, "hello", fallback_models=[])

    assert_that(result.content).is_equal_to("ok")
    provider.complete.assert_called_once()


def test_does_not_try_fallbacks_on_success() -> None:
    """Skip fallback models when primary succeeds."""
    provider = _make_provider()
    provider.complete.return_value = _ok_response()

    result = complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1", "fb-2"],
    )

    assert_that(result.content).is_equal_to("ok")
    assert_that(provider.complete.call_count).is_equal_to(1)
    # Model should be restored
    assert_that(provider.model_name).is_equal_to("primary-model")


# -- TestCompleteWithFallbackChain: Primary fails, fallback models are tried in order.


def test_falls_back_on_provider_error() -> None:
    """Fall back to next model on provider error."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIProviderError("primary down"),
        _ok_response("fb-1"),
    ]

    result = complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1"],
    )

    assert_that(result.content).is_equal_to("ok")
    assert_that(provider.complete.call_count).is_equal_to(2)
    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


def test_falls_back_on_rate_limit_error() -> None:
    """Fall back to next model on rate limit error."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIRateLimitError("rate limited"),
        _ok_response("fb-1"),
    ]

    result = complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1"],
    )

    assert_that(result.content).is_equal_to("ok")
    assert_that(provider.complete.call_count).is_equal_to(2)


def test_tries_multiple_fallbacks_in_order() -> None:
    """Try fallback models sequentially until one succeeds."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIProviderError("primary down"),
        AIRateLimitError("fb-1 rate limited"),
        _ok_response("fb-2"),
    ]

    result = complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1", "fb-2"],
    )

    assert_that(result.content).is_equal_to("ok")
    assert_that(provider.complete.call_count).is_equal_to(3)
    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


def test_model_is_swapped_for_each_fallback() -> None:
    """Verify the provider's model_name is set to each fallback in turn."""
    provider = _make_provider("primary")
    models_seen: list[str] = []

    def capture_model(*args, **kwargs):
        """Record the current model and fail until the third call."""
        models_seen.append(provider.model_name)
        if len(models_seen) < 3:
            raise AIProviderError("fail")
        return _ok_response(provider.model_name)

    provider.complete.side_effect = capture_model

    complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1", "fb-2"],
    )

    assert_that(models_seen).is_equal_to(["primary", "fb-1", "fb-2"])
    assert_that(provider.model_name).is_equal_to("primary")  # restored


# -- TestCompleteWithFallbackAllFail: All models fail -- last error is raised.


def test_raises_last_error_when_all_fail() -> None:
    """Raise the last error when all models fail."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIProviderError("primary down"),
        AIRateLimitError("fb-1 limited"),
        AIProviderError("fb-2 down"),
    ]

    with pytest.raises(AIProviderError, match="fb-2 down"):
        complete_with_fallback(
            provider,
            "hello",
            fallback_models=["fb-1", "fb-2"],
        )

    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


def test_raises_primary_error_when_no_fallbacks() -> None:
    """Raise the primary error when no fallbacks are configured."""
    provider = _make_provider()
    provider.complete.side_effect = AIProviderError("primary down")

    with pytest.raises(AIProviderError, match="primary down"):
        complete_with_fallback(provider, "hello")


# -- TestCompleteWithFallbackAuthError: AIAuthenticationError is never retried.


def test_auth_error_propagates_immediately() -> None:
    """Propagate authentication error without trying fallbacks."""
    provider = _make_provider()
    provider.complete.side_effect = AIAuthenticationError("bad key")

    with pytest.raises(AIAuthenticationError, match="bad key"):
        complete_with_fallback(
            provider,
            "hello",
            fallback_models=["fb-1", "fb-2"],
        )

    # Only one call -- no fallback attempted
    assert_that(provider.complete.call_count).is_equal_to(1)
    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


def test_auth_error_on_fallback_propagates() -> None:
    """Propagate authentication error raised by a fallback model."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIProviderError("primary down"),
        AIAuthenticationError("bad key on fallback"),
    ]

    with pytest.raises(AIAuthenticationError, match="bad key on fallback"):
        complete_with_fallback(
            provider,
            "hello",
            fallback_models=["fb-1"],
        )

    assert_that(provider.complete.call_count).is_equal_to(2)
    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


# -- TestCompleteWithFallbackModelRestoration: model_name restored.


def test_model_restored_on_auth_error() -> None:
    """Restore original model after authentication error."""
    provider = _make_provider("orig")
    provider.complete.side_effect = AIAuthenticationError("err")

    with pytest.raises(AIAuthenticationError):
        complete_with_fallback(
            provider,
            "hello",
            fallback_models=["x"],
        )

    assert_that(provider.model_name).is_equal_to("orig")


def test_model_restored_on_provider_error() -> None:
    """Restore original model after provider error."""
    provider = _make_provider("orig")
    provider.complete.side_effect = AIProviderError("err")

    with pytest.raises(AIProviderError):
        complete_with_fallback(provider, "hello")

    assert_that(provider.model_name).is_equal_to("orig")


def test_model_restored_on_success() -> None:
    """Restore original model after successful fallback."""
    provider = _make_provider("orig")
    provider.complete.side_effect = [
        AIProviderError("fail"),
        _ok_response("fb"),
    ]

    complete_with_fallback(provider, "hello", fallback_models=["fb"])

    assert_that(provider.model_name).is_equal_to("orig")


# -- TestCompleteWithFallbackKwargsPassthrough: kwargs forwarded. -


def test_forwards_all_kwargs() -> None:
    """Forward all keyword arguments to provider.complete."""
    provider = _make_provider()
    provider.complete.return_value = _ok_response()

    complete_with_fallback(
        provider,
        "hello",
        system="sys",
        max_tokens=512,
        timeout=30.0,
    )

    provider.complete.assert_called_once_with(
        "hello",
        system="sys",
        max_tokens=512,
        timeout=30.0,
    )
