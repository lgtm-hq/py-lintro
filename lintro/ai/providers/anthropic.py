"""Anthropic AI provider implementation.

Uses the Anthropic Python SDK to communicate with Claude models.
Requires the ``anthropic`` package (installed via ``lintro[ai]``).
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

_has_anthropic = False
try:
    import anthropic

    _has_anthropic = True
except ImportError:
    pass


@contextmanager
def _map_errors() -> Iterator[None]:
    """Map Anthropic SDK exceptions to AI exceptions.

    Catches ``anthropic.AuthenticationError``, ``anthropic.RateLimitError``,
    and ``anthropic.AnthropicError`` and re-raises them as the corresponding
    AI exception types.
    """
    try:
        yield
    except anthropic.AuthenticationError as e:
        raise AIAuthenticationError(
            f"Anthropic authentication failed: {e}",
        ) from e
    except anthropic.RateLimitError as e:
        raise AIRateLimitError(
            f"Anthropic rate limit exceeded: {e}",
        ) from e
    except anthropic.AnthropicError as e:
        logger.debug(f"Anthropic API error: {e}")
        raise AIProviderError(
            f"Anthropic API error: {e}",
        ) from e

DEFAULT_MODEL = PROVIDERS.anthropic.default_model
DEFAULT_API_KEY_ENV = PROVIDERS.anthropic.default_api_key_env


class AnthropicProvider(BaseAIProvider):
    """Anthropic Claude provider."""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
    ) -> None:
        """Initialize the Anthropic provider.

        Args:
            model: Model identifier. Defaults to claude-sonnet-4-6.
            api_key_env: Environment variable for API key.
                Defaults to ANTHROPIC_API_KEY.
            max_tokens: Default max tokens for completions.
            base_url: Custom API base URL for Anthropic-compatible
                endpoints (proxies, self-hosted, etc.).
        """
        super().__init__(
            provider_name=AIProvider.ANTHROPIC,
            has_sdk=_has_anthropic,
            sdk_package="anthropic",
            default_model=DEFAULT_MODEL,
            default_api_key_env=DEFAULT_API_KEY_ENV,
            model=model,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            base_url=base_url,
        )

    def _create_client(self, *, api_key: str) -> Any:
        """Create the Anthropic SDK client.

        Args:
            api_key: The resolved API key.

        Returns:
            anthropic.Anthropic: The API client.
        """
        kwargs: dict[str, Any] = {"api_key": api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return anthropic.Anthropic(**kwargs)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIResponse:
        """Generate a completion using Claude.

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
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": effective_max,
                "messages": [{"role": "user", "content": prompt}],
                "timeout": timeout,
            }
            if system:
                kwargs["system"] = system

            response = client.messages.create(**kwargs)

            content = ""
            for block in response.content:
                if hasattr(block, "text"):
                    content += block.text

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = estimate_cost(self._model, input_tokens, output_tokens)

            return AIResponse(
                content=content,
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate=cost,
                provider=AIProvider.ANTHROPIC,
            )

    def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIStreamResult:
        """Stream a completion from the Anthropic API token-by-token.

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

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": effective_max,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": timeout,
        }
        if system:
            kwargs["system"] = system

        logger.debug(
            f"Anthropic stream request: model={self._model}, "
            f"max_tokens={effective_max}",
        )

        final_response: list[AIResponse] = []

        def _generate() -> Iterator[str]:
            with _map_errors():
                with client.messages.stream(**kwargs) as stream:
                    yield from stream.text_stream
                    final_message = stream.get_final_message()

                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens
                cost = estimate_cost(self._model, input_tokens, output_tokens)
                final_response.append(
                    AIResponse(
                        content="",
                        model=self._model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_estimate=cost,
                        provider=AIProvider.ANTHROPIC,
                    ),
                )

        def _on_done() -> AIResponse:
            if not final_response:
                raise AIProviderError(
                    "Anthropic stream was not fully consumed",
                )
            return final_response[0]

        return AIStreamResult(
            _chunks=_generate(),
            _on_done=_on_done,
        )
