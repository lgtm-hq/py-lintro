"""OpenAI AI provider implementation.

Uses the OpenAI Python SDK to communicate with GPT models.
Requires the ``openai`` package (installed via ``lintro[ai]``).
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

_has_openai = False
try:
    import openai

    _has_openai = True
except ImportError:
    pass

DEFAULT_MODEL = DEFAULT_MODELS["openai"]
DEFAULT_API_KEY_ENV = DEFAULT_API_KEY_ENVS["openai"]


class OpenAIProvider(BaseAIProvider):
    """OpenAI GPT provider."""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        """Initialize the OpenAI provider.

        Args:
            model: Model identifier. Defaults to gpt-4o.
            api_key_env: Environment variable for API key.
                Defaults to OPENAI_API_KEY.
            max_tokens: Default max tokens for completions.

        Raises:
            AINotAvailableError: If the openai package is not installed.
        """
        if not _has_openai:
            raise AINotAvailableError(
                "OpenAI provider requires the 'openai' package. "
                "Install with: uv pip install 'lintro[ai]'",
            )

        self._model = model or DEFAULT_MODEL
        self._api_key_env = api_key_env or DEFAULT_API_KEY_ENV
        self._max_tokens = max_tokens
        self._client: Any = None

    def _get_client(self) -> Any:
        """Get or create the OpenAI client.

        Returns:
            openai.OpenAI: The API client.

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

        self._client = openai.OpenAI(api_key=api_key)
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        timeout: float = 60.0,
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

        try:
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
                provider="openai",
            )

        except openai.AuthenticationError as e:
            raise AIAuthenticationError(
                f"OpenAI authentication failed: {e}",
            ) from e
        except openai.RateLimitError as e:
            raise AIRateLimitError(
                f"OpenAI rate limit exceeded: {e}",
            ) from e
        except openai.APIError as e:
            logger.debug(f"OpenAI API error: {e}")
            raise AIProviderError(
                f"OpenAI API error: {e}",
            ) from e

    def is_available(self) -> bool:
        """Check if OpenAI is ready to use.

        Returns:
            bool: True if the SDK is installed and an API key is set.
        """
        if not _has_openai:
            return False
        return bool(os.environ.get(self._api_key_env))

    @property
    def name(self) -> str:
        """Return the provider name.

        Returns:
            str: "openai".
        """
        return "openai"

    @property
    def model_name(self) -> str:
        """Return the configured model name.

        Returns:
            str: The model identifier.
        """
        return self._model
