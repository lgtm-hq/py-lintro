"""Anthropic AI provider implementation.

Uses the Anthropic Python SDK to communicate with Claude models.
Requires the ``anthropic`` package (installed via ``lintro[ai]``).
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from lintro.ai.cost import estimate_cost
from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.providers import DEFAULT_API_KEY_ENVS, DEFAULT_MODELS
from lintro.ai.providers.base import AIResponse, BaseAIProvider

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

DEFAULT_MODEL = DEFAULT_MODELS["anthropic"]
DEFAULT_API_KEY_ENV = DEFAULT_API_KEY_ENVS["anthropic"]


class AnthropicProvider(BaseAIProvider):
    """Anthropic Claude provider.

    Attributes:
        _model: Model identifier to use.
        _api_key_env: Environment variable name for the API key.
        _max_tokens: Default max tokens for completions.
        _client: Lazy-initialized Anthropic client.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        """Initialize the Anthropic provider.

        Args:
            model: Model identifier. Defaults to claude-sonnet-4-20250514.
            api_key_env: Environment variable for API key.
                Defaults to ANTHROPIC_API_KEY.
            max_tokens: Default max tokens for completions.

        Raises:
            AINotAvailableError: If the anthropic package is not installed.
        """
        if anthropic is None:
            raise AINotAvailableError(
                "Anthropic provider requires the 'anthropic' package. "
                "Install with: uv pip install 'lintro[ai]'",
            )

        self._model = model or DEFAULT_MODEL
        self._api_key_env = api_key_env or DEFAULT_API_KEY_ENV
        self._max_tokens = max_tokens
        self._client: Any = None

    def _get_client(self) -> Any:
        """Get or create the Anthropic client.

        Returns:
            anthropic.Anthropic: The API client.

        Raises:
            AIAuthenticationError: If no API key is found.
        """
        if self._client is not None:
            return self._client

        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise AIAuthenticationError(
                f"No API key found. Set the {self._api_key_env} "
                f"environment variable.",
            )

        self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse:
        """Generate a completion using Claude.

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.

        Returns:
            AIResponse: The model's response with usage metadata.

        Raises:
            AIAuthenticationError: If authentication fails.
            AIRateLimitError: If rate limited.
            AIProviderError: If the API call fails.
        """
        client = self._get_client()
        effective_max = min(max_tokens, self._max_tokens)

        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": effective_max,
                "messages": [{"role": "user", "content": prompt}],
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
                provider="anthropic",
            )

        except anthropic.AuthenticationError as e:
            raise AIAuthenticationError(
                f"Anthropic authentication failed: {e}",
            ) from e
        except anthropic.RateLimitError as e:
            raise AIRateLimitError(
                f"Anthropic rate limit exceeded: {e}",
            ) from e
        except anthropic.APIError as e:
            logger.debug(f"Anthropic API error: {e}")
            raise AIProviderError(
                f"Anthropic API error: {e}",
            ) from e

    def is_available(self) -> bool:
        """Check if Anthropic is ready to use.

        Returns:
            bool: True if the SDK is installed and an API key is set.
        """
        if anthropic is None:
            return False
        return bool(os.environ.get(self._api_key_env))

    @property
    def name(self) -> str:
        """Return the provider name.

        Returns:
            str: "anthropic".
        """
        return "anthropic"

    @property
    def model_name(self) -> str:
        """Return the configured model name.

        Returns:
            str: The model identifier.
        """
        return self._model
