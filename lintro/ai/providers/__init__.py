"""AI provider factory and registry.

Provides the ``get_provider()`` factory function that instantiates
the appropriate AI provider based on configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.exceptions import AINotAvailableError  # noqa: F401 -- public re-export

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider

# Single source of truth for provider defaults.
# Used by provider implementations and by the pre-execution summary
# (which needs these values without importing heavy SDKs).
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
}

DEFAULT_API_KEY_ENVS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def get_provider(config: AIConfig) -> BaseAIProvider:
    """Instantiate an AI provider from configuration.

    Args:
        config: AI configuration specifying provider, model, and API key.

    Returns:
        BaseAIProvider: Configured provider instance.

    Raises:
        AINotAvailableError: If the provider's package is not installed.
        ValueError: If the provider name is not recognized.
    """
    provider_name = config.provider.lower()

    if provider_name == "anthropic":
        from lintro.ai.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            model=config.model,
            api_key_env=config.api_key_env,
            max_tokens=config.max_tokens,
        )
    elif provider_name == "openai":
        from lintro.ai.providers.openai import OpenAIProvider

        return OpenAIProvider(
            model=config.model,
            api_key_env=config.api_key_env,
            max_tokens=config.max_tokens,
        )
    else:
        supported = "anthropic, openai"
        raise ValueError(
            f"Unknown AI provider: '{provider_name}'. "
            f"Supported providers: {supported}",
        )


def get_default_model(provider_name: str) -> str | None:
    """Get the default model for a provider without importing its SDK.

    Args:
        provider_name: Provider name (e.g. "anthropic", "openai").

    Returns:
        Default model identifier, or None if provider is unknown.
    """
    return DEFAULT_MODELS.get(provider_name.lower())


__all__ = ["get_default_model", "get_provider", "AINotAvailableError"]
