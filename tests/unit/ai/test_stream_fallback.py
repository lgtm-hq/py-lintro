"""Tests for stream_complete_with_fallback()."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import AIProviderError
from lintro.ai.fallback import stream_complete_with_fallback
from lintro.ai.providers.base import AIResponse, AIStreamResult, BaseAIProvider
from lintro.ai.providers.constants import DEFAULT_PER_CALL_MAX_TOKENS, DEFAULT_TIMEOUT


def _make_response(content: str = "ok", provider: str = "stub") -> AIResponse:
    return AIResponse(
        content=content,
        model="m",
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        provider=provider,
    )


class _SuccessProvider(BaseAIProvider):
    """Provider that always succeeds."""

    def __init__(self, name: str = "success") -> None:
        self._name = name
        self._provider_name = name
        self._has_sdk = True
        self._model = "test-model"
        self._api_key_env = "TEST_KEY"
        self._max_tokens = DEFAULT_PER_CALL_MAX_TOKENS
        self._base_url = None
        self._client = "fake"

    def _create_client(self, *, api_key: str) -> object:
        return "fake"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIResponse:
        return _make_response(content=f"from-{self._name}", provider=self._name)

    def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIStreamResult:
        resp = _make_response(content="", provider=self._name)
        return AIStreamResult(
            _chunks=iter([f"chunk-{self._name}"]),
            _on_done=lambda: resp,
        )


class _FailingProvider(BaseAIProvider):
    """Provider that always raises."""

    def __init__(self) -> None:
        self._provider_name = "failing"
        self._has_sdk = True
        self._model = "fail-model"
        self._api_key_env = "FAIL_KEY"
        self._max_tokens = DEFAULT_PER_CALL_MAX_TOKENS
        self._base_url = None
        self._client = "fake"

    def _create_client(self, *, api_key: str) -> object:
        return "fake"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIResponse:
        raise AIProviderError("provider down")

    def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIStreamResult:
        raise AIProviderError("stream provider down")


def test_stream_fallback_returns_first_success() -> None:
    """Return the stream from the first working provider."""
    provider = _SuccessProvider("primary")
    result = stream_complete_with_fallback(provider, "prompt")

    chunks = list(result)
    assert_that(chunks).is_equal_to(["chunk-primary"])


def test_stream_fallback_tries_fallback_models() -> None:
    """Falls back to alternate model when primary fails."""
    calls: list[str] = []

    class _ModelTrackingProvider(_SuccessProvider):
        def stream_complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
            timeout: float = DEFAULT_TIMEOUT,
        ) -> AIStreamResult:
            calls.append(self._model)
            if self._model == "test-model":
                raise AIProviderError("primary failed")
            return super().stream_complete(
                prompt,
                system=system,
                max_tokens=max_tokens,
                timeout=timeout,
            )

    provider = _ModelTrackingProvider("tracker")
    result = stream_complete_with_fallback(
        provider,
        "prompt",
        fallback_models=["fallback-model"],
    )

    chunks = list(result)
    assert_that(chunks).is_equal_to(["chunk-tracker"])
    assert_that(calls).is_equal_to(["test-model", "fallback-model"])


def test_stream_fallback_raises_when_all_fail() -> None:
    """Raise AIProviderError when all providers fail."""
    provider = _FailingProvider()

    with pytest.raises(AIProviderError, match="stream provider down"):
        stream_complete_with_fallback(provider, "prompt")


def test_stream_fallback_restores_model_name() -> None:
    """Provider model name is restored after fallback completes."""
    provider = _SuccessProvider("p1")
    original_model = provider.model_name

    result = stream_complete_with_fallback(
        provider,
        "prompt",
        fallback_models=["other-model"],
    )
    list(result)  # consume stream so fallback logic completes

    assert_that(provider.model_name).is_equal_to(original_model)
