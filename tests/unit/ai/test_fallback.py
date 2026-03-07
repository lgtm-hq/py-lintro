"""Tests for the runtime model fallback chain."""

# ruff: noqa: D102

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.fallback import complete_with_fallback
from lintro.ai.providers.base import AIResponse


def _make_provider(model: str = "primary-model") -> MagicMock:
    """Create a mock provider with a configurable _model attribute."""
    provider = MagicMock()
    provider._model = model
    return provider


def _ok_response(model: str = "primary-model") -> AIResponse:
    return AIResponse(
        content="ok",
        model=model,
        input_tokens=10,
        output_tokens=5,
        cost_estimate=0.001,
        provider="mock",
    )


class TestCompleteWithFallbackPrimarySuccess:
    """Primary model succeeds on first try."""

    def test_returns_response_without_fallback(self) -> None:
        provider = _make_provider()
        provider.complete.return_value = _ok_response()

        result = complete_with_fallback(provider, "hello")

        assert result.content == "ok"
        provider.complete.assert_called_once()

    def test_returns_response_with_empty_fallback_list(self) -> None:
        provider = _make_provider()
        provider.complete.return_value = _ok_response()

        result = complete_with_fallback(provider, "hello", fallback_models=[])

        assert result.content == "ok"
        provider.complete.assert_called_once()

    def test_does_not_try_fallbacks_on_success(self) -> None:
        provider = _make_provider()
        provider.complete.return_value = _ok_response()

        result = complete_with_fallback(
            provider,
            "hello",
            fallback_models=["fb-1", "fb-2"],
        )

        assert result.content == "ok"
        assert provider.complete.call_count == 1
        # Model should be restored
        assert provider._model == "primary-model"


class TestCompleteWithFallbackChain:
    """Primary fails, fallback models are tried in order."""

    def test_falls_back_on_provider_error(self) -> None:
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

        assert result.content == "ok"
        assert provider.complete.call_count == 2
        assert provider._model == "primary-model"  # restored

    def test_falls_back_on_rate_limit_error(self) -> None:
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

        assert result.content == "ok"
        assert provider.complete.call_count == 2

    def test_tries_multiple_fallbacks_in_order(self) -> None:
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

        assert result.content == "ok"
        assert provider.complete.call_count == 3
        assert provider._model == "primary-model"  # restored

    def test_model_is_swapped_for_each_fallback(self) -> None:
        """Verify the provider's _model is set to each fallback in turn."""
        provider = _make_provider("primary")
        models_seen: list[str] = []

        def capture_model(*args, **kwargs):
            models_seen.append(provider._model)
            if len(models_seen) < 3:
                raise AIProviderError("fail")
            return _ok_response(provider._model)

        provider.complete.side_effect = capture_model

        complete_with_fallback(
            provider,
            "hello",
            fallback_models=["fb-1", "fb-2"],
        )

        assert models_seen == ["primary", "fb-1", "fb-2"]
        assert provider._model == "primary"  # restored


class TestCompleteWithFallbackAllFail:
    """All models fail -- last error is raised."""

    def test_raises_last_error_when_all_fail(self) -> None:
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

        assert provider._model == "primary-model"  # restored

    def test_raises_primary_error_when_no_fallbacks(self) -> None:
        provider = _make_provider()
        provider.complete.side_effect = AIProviderError("primary down")

        with pytest.raises(AIProviderError, match="primary down"):
            complete_with_fallback(provider, "hello")


class TestCompleteWithFallbackAuthError:
    """AIAuthenticationError is never retried."""

    def test_auth_error_propagates_immediately(self) -> None:
        provider = _make_provider()
        provider.complete.side_effect = AIAuthenticationError("bad key")

        with pytest.raises(AIAuthenticationError, match="bad key"):
            complete_with_fallback(
                provider,
                "hello",
                fallback_models=["fb-1", "fb-2"],
            )

        # Only one call -- no fallback attempted
        assert provider.complete.call_count == 1
        assert provider._model == "primary-model"  # restored

    def test_auth_error_on_fallback_propagates(self) -> None:
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

        assert provider.complete.call_count == 2
        assert provider._model == "primary-model"  # restored


class TestCompleteWithFallbackModelRestoration:
    """_model is always restored, even on error."""

    def test_model_restored_on_auth_error(self) -> None:
        provider = _make_provider("orig")
        provider.complete.side_effect = AIAuthenticationError("err")

        with pytest.raises(AIAuthenticationError):
            complete_with_fallback(
                provider,
                "hello",
                fallback_models=["x"],
            )

        assert provider._model == "orig"

    def test_model_restored_on_provider_error(self) -> None:
        provider = _make_provider("orig")
        provider.complete.side_effect = AIProviderError("err")

        with pytest.raises(AIProviderError):
            complete_with_fallback(provider, "hello")

        assert provider._model == "orig"

    def test_model_restored_on_success(self) -> None:
        provider = _make_provider("orig")
        provider.complete.side_effect = [
            AIProviderError("fail"),
            _ok_response("fb"),
        ]

        complete_with_fallback(provider, "hello", fallback_models=["fb"])

        assert provider._model == "orig"


class TestCompleteWithFallbackKwargsPassthrough:
    """Keyword arguments are forwarded to provider.complete()."""

    def test_forwards_all_kwargs(self) -> None:
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
