"""AI provider factory and registry.

Provides the ``get_provider()`` factory function that instantiates
the appropriate AI provider based on configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.exceptions import AINotAvailableError  # noqa: F401 -- public re-export
from lintro.ai.registry import PROVIDERS, AIProvider

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider

# Re-export for backward compatibility with string-keyed access.
DEFAULT_MODELS: dict[str, str] = {
    p.value: m for p, m in PROVIDERS.default_models.items()
}
DEFAULT_API_KEY_ENVS: dict[str, str] = {
    p.value: e for p, e in PROVIDERS.default_api_key_envs.items()
}


def get_provider(config: AIConfig) -> BaseAIProvider:
    """Instantiate an AI provider from configuration.

    Args:
        config: AI configuration specifying provider, model, and API key.

    Returns:
        BaseAIProvider: Configured provider instance.

    Raises:
        ValueError: If the provider name is not recognized.
    """
    try:
        provider_enum = AIProvider(config.provider.lower())
    except ValueError as exc:
        supported = ", ".join(p.value for p in AIProvider)
        raise ValueError(
            f"Unknown AI provider: '{config.provider}'. "
            f"Supported providers: {supported}",
        ) from exc

    if provider_enum is AIProvider.ANTHROPIC:
        from lintro.ai.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            model=config.model,
            api_key_env=config.api_key_env,
            max_tokens=config.max_tokens,
            base_url=config.api_base_url,
        )
    elif provider_enum is AIProvider.OPENAI:
        from lintro.ai.providers.openai import OpenAIProvider

        return OpenAIProvider(
            model=config.model,
            api_key_env=config.api_key_env,
            max_tokens=config.max_tokens,
            base_url=config.api_base_url,
        )
    else:
        # Unreachable with current AIProvider members, but kept as a safety net.
        supported = ", ".join(p.value for p in AIProvider)
        raise ValueError(
            f"Unknown AI provider: '{provider_enum}'. "
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
