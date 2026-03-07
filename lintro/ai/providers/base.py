"""Abstract base class for AI providers.

Defines the contract that all AI provider implementations must follow.
Shared initialisation, API-key resolution, and availability logic live
here so that concrete providers only implement SDK-specific pieces.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from lintro.ai.exceptions import AIAuthenticationError, AINotAvailableError


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
    provider: str = field(default="")


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
        max_tokens: int = 4096,
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

        api_key = os.environ.get(self._api_key_env)
        if not api_key:
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
        max_tokens: int = 1024,
        timeout: float = 60.0,
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

    # -- Concrete shared helpers -------------------------------------------

    def is_available(self) -> bool:
        """Check if this provider is ready to use.

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
