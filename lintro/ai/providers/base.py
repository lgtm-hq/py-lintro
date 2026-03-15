"""Abstract base class for AI providers.

Defines the contract that all AI provider implementations must follow.
Shared initialisation, API-key resolution, and availability logic live
here so that concrete providers only implement SDK-specific pieces.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from lintro.ai.exceptions import AIAuthenticationError, AINotAvailableError
from lintro.ai.providers.constants import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_PER_CALL_MAX_TOKENS,
    DEFAULT_TIMEOUT,
)
from lintro.ai.registry import AIProvider


@dataclass
class AIResponse:
    """Response from an AI provider API call.

    Attributes:
        content: The generated text content.
        model: Model identifier that produced this response.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.
        cost_estimate: Estimated cost in USD for this call.
        provider: Name of the provider (e.g., "anthropic", "openai").
    """

    content: str
    model: str
    input_tokens: int = field(default=0)
    output_tokens: int = field(default=0)
    cost_estimate: float = field(default=0.0)
    provider: AIProvider | str = field(default="")


@dataclass
class AIStreamResult:
    """Wraps a token iterator and provides finalized metadata after exhaustion."""

    _chunks: Iterator[str]
    _on_done: Callable[[], AIResponse]
    _consumed: bool = field(default=False, init=False)

    def __iter__(self) -> Iterator[str]:
        """Yield text chunks from the underlying iterator."""
        yield from self._chunks
        self._consumed = True

    def response(self) -> AIResponse:
        """Return the finalized AIResponse.

        Only valid after iteration completes.

        Returns:
            The finalized AIResponse with usage metadata.
        """
        return self._on_done()

    def collect(self) -> AIResponse:
        """Consume all tokens and return the complete AIResponse.

        May only be called once — a second call raises ``RuntimeError``
        because the underlying iterator has already been exhausted.

        Returns:
            AIResponse with concatenated content and usage metadata.

        Raises:
            RuntimeError: If the stream has already been consumed.
        """
        if self._consumed:
            raise RuntimeError("AIStreamResult already consumed")
        content = "".join(self)
        resp = self.response()
        return AIResponse(
            content=content,
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_estimate=resp.cost_estimate,
            provider=resp.provider,
        )


class BaseAIProvider(ABC):
    """Abstract base class for AI providers.

    Handles common initialisation (model, API-key env var, max tokens,
    base URL), lazy client creation with API-key validation, and the
    ``is_available`` / property boilerplate.

    Subclasses must implement:
    * ``_create_client()`` -- return an SDK-specific client instance.
    * ``complete()`` -- perform the SDK-specific API call and map errors.
    """

    def __init__(
        self,
        *,
        provider_name: str,
        has_sdk: bool,
        sdk_package: str,
        default_model: str,
        default_api_key_env: str,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
    ) -> None:
        """Initialise the provider with shared parameters.

        Args:
            provider_name: Human-readable provider name (e.g. "anthropic").
            has_sdk: Whether the provider SDK was successfully imported.
            sdk_package: Package name shown in the install hint.
            default_model: Fallback model when *model* is ``None``.
            default_api_key_env: Fallback env-var name when *api_key_env*
                is ``None``.
            model: Model identifier override.
            api_key_env: Environment variable for the API key override.
                The key is required at runtime; its absence raises
                ``AIAuthenticationError`` on first API call.
            max_tokens: Provider-level cap on generated tokens.
            base_url: Custom API base URL.

        Raises:
            AINotAvailableError: If the SDK is not installed.
        """
        if not has_sdk:
            raise AINotAvailableError(
                f"{provider_name.title()} provider requires the "
                f"'{sdk_package}' package. "
                "Install with: uv pip install 'lintro[ai]'",
            )

        self._provider_name = provider_name
        self._has_sdk = has_sdk
        self._model = model or default_model
        self._api_key_env = api_key_env or default_api_key_env
        self._max_tokens = max_tokens
        self._base_url = base_url
        self._client: Any = None

    # -- Client management -------------------------------------------------

    def _get_client(self) -> Any:
        """Get or lazily create the SDK client.

        Returns:
            The SDK client instance.

        Raises:
            AIAuthenticationError: If no API key is found.
        """
        if self._client is not None:
            return self._client

        api_key = os.environ.get(self._api_key_env) or ""
        if not api_key and not self._base_url:
            raise AIAuthenticationError(
                f"No API key found. Set the {self._api_key_env} "
                f"environment variable.",
            )

        self._client = self._create_client(api_key=api_key)
        return self._client

    @abstractmethod
    def _create_client(self, *, api_key: str) -> Any:
        """Create the SDK-specific client.

        Args:
            api_key: The resolved API key.

        Returns:
            An SDK client instance.
        """
        ...

    # -- Abstract: SDK-specific completion ---------------------------------

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIResponse:
        """Generate a completion from the AI model.

        Args:
            prompt: The user prompt to send to the model.
            system: Optional system prompt to set context.
            max_tokens: Maximum number of tokens to generate.
            timeout: Request timeout in seconds.

        Returns:
            AIResponse: The model's response with usage metadata.

        Raises:
            AIProviderError: If the API call fails.
            AIAuthenticationError: If authentication fails.
            AIRateLimitError: If rate limited.
        """
        ...

    # -- Streaming (default delegates to complete) --------------------------

    def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIStreamResult:
        """Stream a completion. Default: delegates to complete().

        Providers with native streaming support should override this.

        Args:
            prompt: The user prompt text.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.

        Returns:
            An AIStreamResult wrapping the token stream.
        """
        response = self.complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return AIStreamResult(
            _chunks=iter([response.content]),
            _on_done=lambda: response,
        )

    # -- Concrete shared helpers -------------------------------------------

    def is_available(self) -> bool:
        """Check if this provider is ready to use.

        Note: this reads the API key from the environment on every call,
        while ``_get_client()`` caches the client (and its key) after
        the first successful creation. If the env var is removed after
        client creation, ``is_available()`` will return ``False`` even
        though the cached client would still work.

        Returns:
            bool: True if the provider's SDK is installed and an API
                key is configured.
        """
        if not self._has_sdk:
            return False
        return bool(os.environ.get(self._api_key_env))

    @property
    def name(self) -> str:
        """Return the provider's name.

        Returns:
            str: Provider identifier (e.g., "anthropic", "openai").
        """
        return self._provider_name

    @property
    def model_name(self) -> str:
        """Return the configured model name.

        Returns:
            str: Model identifier being used.
        """
        return self._model

    @model_name.setter
    def model_name(self, value: str) -> None:
        """Set the model name.

        Args:
            value: New model identifier.
        """
        self._model = value
