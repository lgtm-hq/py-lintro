"""Tests for stream_complete_with_fallback()."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import AIProviderError
from lintro.ai.fallback import stream_complete_with_fallback
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.providers.base import AIResponse, AsyncAIStreamResult, BaseAIProvider
from lintro.ai.providers.constants import DEFAULT_PER_CALL_MAX_TOKENS, DEFAULT_TIMEOUT


def _make_response(content: str = "ok", provider: str = "stub") -> AIResponse:
    """Build a stub provider response.

    Args:
        content: Response content.
        provider: Provider identifier recorded on the response.

    Returns:
        A populated ``AIResponse``.
    """
    return AIResponse(
        content=content,
        model="m",
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        provider=provider,
    )


async def _one_chunk(text: str) -> AsyncIterator[str]:
    """Yield a single chunk.

    Args:
        text: The chunk to yield.

    Yields:
        str: The chunk.
    """
    yield text


class _SuccessProvider(BaseAIProvider):
    """Provider that always succeeds."""

    def __init__(self, name: str = "success") -> None:
        """Initialise the stub provider.

        Args:
            name: Provider identifier used in responses and chunks.
        """
        self._name = name
        self._provider_name = name
        self._has_sdk = True
        self._model = "test-model"
        self._api_key_env = "TEST_KEY"
        self._max_tokens = DEFAULT_PER_CALL_MAX_TOKENS
        self._base_url = None
        self._client = "fake"
        self._client_loop = None

    def _create_client(self, *, api_key: str) -> object:
        """Return a stub client.

        Args:
            api_key: Ignored API key.

        Returns:
            A stub client marker.
        """
        return "fake"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        repo_root: str | None = None,
        use_one_shot: bool = False,
        model: str | None = None,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        """Return a canned response.

        Args:
            prompt: Ignored user prompt.
            system: Ignored system prompt.
            max_tokens: Ignored token cap.
            timeout: Ignored timeout.
            repo_root: Ignored repository root.
            use_one_shot: Ignored session flag.
            model: Ignored model override.
            cli_schema: Ignored schema request.

        Returns:
            A canned response naming this provider.
        """
        del model, cli_schema
        return _make_response(content=f"from-{self._name}", provider=self._name)

    async def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        model: str | None = None,
    ) -> AsyncAIStreamResult:
        """Return a one-chunk async stream.

        Args:
            prompt: Ignored user prompt.
            system: Ignored system prompt.
            max_tokens: Ignored token cap.
            timeout: Ignored timeout.
            model: Ignored model override.

        Returns:
            A stream yielding one chunk naming this provider.
        """
        del model
        resp = _make_response(content="", provider=self._name)
        return AsyncAIStreamResult(
            _chunks=_one_chunk(f"chunk-{self._name}"),
            _on_done=lambda: resp,
        )


class _FailingProvider(BaseAIProvider):
    """Provider that always raises."""

    def __init__(self) -> None:
        """Initialise the failing stub provider."""
        self._provider_name = "failing"
        self._has_sdk = True
        self._model = "fail-model"
        self._api_key_env = "FAIL_KEY"
        self._max_tokens = DEFAULT_PER_CALL_MAX_TOKENS
        self._base_url = None
        self._client = "fake"
        self._client_loop = None

    def _create_client(self, *, api_key: str) -> object:
        """Return a stub client.

        Args:
            api_key: Ignored API key.

        Returns:
            A stub client marker.
        """
        return "fake"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        repo_root: str | None = None,
        use_one_shot: bool = False,
        model: str | None = None,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        """Always fail.

        Args:
            prompt: Ignored user prompt.
            system: Ignored system prompt.
            max_tokens: Ignored token cap.
            timeout: Ignored timeout.
            repo_root: Ignored repository root.
            use_one_shot: Ignored session flag.
            model: Ignored model override.
            cli_schema: Ignored schema request.

        Returns:
            Never returns.

        Raises:
            AIProviderError: Always.
        """
        del model, cli_schema
        raise AIProviderError("provider down")

    async def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        model: str | None = None,
    ) -> AsyncAIStreamResult:
        """Always fail before a stream is opened.

        Args:
            prompt: Ignored user prompt.
            system: Ignored system prompt.
            max_tokens: Ignored token cap.
            timeout: Ignored timeout.
            model: Ignored model override.

        Returns:
            Never returns.

        Raises:
            AIProviderError: Always.
        """
        del model
        raise AIProviderError("stream provider down")


async def test_stream_fallback_returns_first_success() -> None:
    """Return the stream from the first working provider."""
    provider = _SuccessProvider("primary")
    result = await stream_complete_with_fallback(provider, "prompt")

    chunks = [chunk async for chunk in result]
    assert_that(chunks).is_equal_to(["chunk-primary"])


async def test_stream_fallback_tries_fallback_models() -> None:
    """Falls back to alternate model when primary fails."""
    calls: list[str] = []

    class _ModelTrackingProvider(_SuccessProvider):
        """Stub that fails on the primary model and records attempts."""

        async def stream_complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
            timeout: float = DEFAULT_TIMEOUT,
            model: str | None = None,
        ) -> AsyncAIStreamResult:
            """Fail on the primary model, succeed on any fallback.

            Args:
                prompt: The user prompt.
                system: Optional system prompt.
                max_tokens: Token cap for the call.
                timeout: Request timeout in seconds.
                model: Model override for this attempt.

            Returns:
                A one-chunk stream for a fallback model.

            Raises:
                AIProviderError: When the primary model is used.
            """
            effective_model = model or self._model
            calls.append(effective_model)
            if effective_model == "test-model":
                raise AIProviderError("primary failed")
            return await super().stream_complete(
                prompt,
                system=system,
                max_tokens=max_tokens,
                timeout=timeout,
                model=model,
            )

    provider = _ModelTrackingProvider("tracker")
    result = await stream_complete_with_fallback(
        provider,
        "prompt",
        fallback_models=["fallback-model"],
    )

    chunks = [chunk async for chunk in result]
    assert_that(chunks).is_equal_to(["chunk-tracker"])
    assert_that(calls).is_equal_to(["test-model", "fallback-model"])


async def test_stream_fallback_raises_when_all_fail() -> None:
    """Raise AIProviderError when all providers fail."""
    provider = _FailingProvider()

    with pytest.raises(AIProviderError, match="stream provider down"):
        await stream_complete_with_fallback(provider, "prompt")


async def test_stream_fallback_restores_model_name() -> None:
    """Provider model name is restored after fallback completes."""

    class _FailThenSuccessProvider(_SuccessProvider):
        """Stub that fails on the primary model only."""

        async def stream_complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
            timeout: float = DEFAULT_TIMEOUT,
            model: str | None = None,
        ) -> AsyncAIStreamResult:
            """Fail on the primary model, succeed on any fallback.

            Args:
                prompt: The user prompt.
                system: Optional system prompt.
                max_tokens: Token cap for the call.
                timeout: Request timeout in seconds.
                model: Model override for this attempt.

            Returns:
                A one-chunk stream for a fallback model.

            Raises:
                AIProviderError: When the primary model is used.
            """
            effective_model = model or self._model
            if effective_model == "test-model":
                raise AIProviderError("primary failed")
            return await super().stream_complete(
                prompt,
                system=system,
                max_tokens=max_tokens,
                timeout=timeout,
                model=model,
            )

    provider = _FailThenSuccessProvider("p1")
    original_model = provider.model_name

    result = await stream_complete_with_fallback(
        provider,
        "prompt",
        fallback_models=["other-model"],
    )
    [chunk async for chunk in result]  # consume so fallback logic completes

    assert_that(provider.model_name).is_equal_to(original_model)
