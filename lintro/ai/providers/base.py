"""Abstract base class for AI providers.

Defines the contract that all AI provider implementations must follow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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

    All provider implementations must implement ``complete()`` for
    synchronous text generation and ``is_available()`` to check
    whether the provider's dependencies are installed and configured.
    """

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse:
        """Generate a completion from the AI model.

        Args:
            prompt: The user prompt to send to the model.
            system: Optional system prompt to set context.
            max_tokens: Maximum number of tokens to generate.

        Returns:
            AIResponse: The model's response with usage metadata.

        Raises:
            AIProviderError: If the API call fails.
            AIAuthenticationError: If authentication fails.
            AIRateLimitError: If rate limited.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is ready to use.

        Returns:
            bool: True if the provider's SDK is installed and an API
                key is configured.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider's name.

        Returns:
            str: Provider identifier (e.g., "anthropic", "openai").
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the configured model name.

        Returns:
            str: Model identifier being used.
        """
        ...
