"""OpenAI AI provider implementation.

Uses the OpenAI Python SDK to communicate with GPT models.
Requires the ``openai`` package (installed via ``lintro[ai]``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from loguru import logger

from lintro.ai.cost import estimate_cost
from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.providers.base import AIResponse, AIStreamResult, BaseAIProvider
from lintro.ai.providers.constants import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_PER_CALL_MAX_TOKENS,
    DEFAULT_TIMEOUT,
)
from lintro.ai.registry import PROVIDERS, AIProvider

_has_openai = False
try:
    import openai

    _has_openai = True
except ImportError:
    pass


@contextmanager
def _map_errors() -> Iterator[None]:
    """Map OpenAI SDK exceptions to AI exceptions.

    Catches ``openai.AuthenticationError``, ``openai.RateLimitError``,
    and ``openai.OpenAIError`` and re-raises them as the corresponding
    AI exception types.
    """
    try:
        yield
    except openai.AuthenticationError as e:
        raise AIAuthenticationError(
            f"OpenAI authentication failed: {e}",
        ) from e
    except openai.RateLimitError as e:
        raise AIRateLimitError(
            f"OpenAI rate limit exceeded: {e}",
        ) from e
    except openai.OpenAIError as e:
        logger.debug(f"OpenAI API error: {e}")
        raise AIProviderError(
            f"OpenAI API error: {e}",
        ) from e

DEFAULT_MODEL = PROVIDERS.openai.default_model
DEFAULT_API_KEY_ENV = PROVIDERS.openai.default_api_key_env


class OpenAIProvider(BaseAIProvider):
    """OpenAI GPT provider."""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
    ) -> None:
        """Initialize the OpenAI provider.

        Args:
            model: Model identifier. Defaults to gpt-4o.
            api_key_env: Environment variable for API key.
                Defaults to OPENAI_API_KEY.
            max_tokens: Default max tokens for completions.
            base_url: Custom API base URL for OpenAI-compatible
                endpoints (Ollama, vLLM, Azure OpenAI, etc.).
        """
        super().__init__(
            provider_name=AIProvider.OPENAI,
            has_sdk=_has_openai,
            sdk_package="openai",
            default_model=DEFAULT_MODEL,
            default_api_key_env=DEFAULT_API_KEY_ENV,
            model=model,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            base_url=base_url,
        )

    def _create_client(self, *, api_key: str) -> Any:
        """Create the OpenAI SDK client.

        Args:
            api_key: The resolved API key.

        Returns:
            openai.OpenAI: The API client.
        """
        kwargs: dict[str, Any] = {"api_key": api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return openai.OpenAI(**kwargs)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIResponse:
        """Generate a completion using GPT.

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.

        Returns:
            AIResponse: The model's response with usage metadata.

        Raises:
            AIAuthenticationError: If authentication fails.
            AIRateLimitError: If rate limited.
            AIProviderError: If the API call fails.
        """
        client = self._get_client()
        # Per-call cap: the lower of the caller's request and the
        # provider-level cap set at init time.
        effective_max = min(max_tokens, self._max_tokens)

        with _map_errors():
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=effective_max,
                timeout=timeout,
            )

            content = response.choices[0].message.content or ""

            input_tokens = 0
            output_tokens = 0
            if response.usage:
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens

            cost = estimate_cost(self._model, input_tokens, output_tokens)

            return AIResponse(
                content=content,
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate=cost,
                provider=AIProvider.OPENAI,
            )

    def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIStreamResult:
        """Stream a completion from the OpenAI API token-by-token.

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.

        Returns:
            An AIStreamResult wrapping the token stream.
        """
        client = self._get_client()
        effective_max = min(max_tokens, self._max_tokens)

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        logger.debug(
            f"OpenAI stream request: model={self._model}, "
            f"max_tokens={effective_max}",
        )

        final_response: list[AIResponse] = []

        def _generate() -> Iterator[str]:
            with _map_errors():
                stream = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    max_tokens=effective_max,
                    timeout=timeout,
                    stream=True,
                    stream_options={"include_usage": True},
                )

                input_tokens = 0
                output_tokens = 0

                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                    if chunk.usage:
                        input_tokens = chunk.usage.prompt_tokens
                        output_tokens = chunk.usage.completion_tokens

                cost = estimate_cost(self._model, input_tokens, output_tokens)
                final_response.append(
                    AIResponse(
                        content="",
                        model=self._model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_estimate=cost,
                        provider=AIProvider.OPENAI,
                    ),
                )

        def _on_done() -> AIResponse:
            if not final_response:
                raise AIProviderError(
                    "OpenAI stream was not fully consumed",
                )
            return final_response[0]

        return AIStreamResult(
            _chunks=_generate(),
            _on_done=_on_done,
        )
