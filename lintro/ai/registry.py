"""AI provider registry — single source of truth for provider metadata.

Consolidates model pricing, default models, and API key environment
variables into a frozen dataclass hierarchy keyed by an ``AIProvider``
StrEnum.  Every piece of provider metadata lives here; downstream
modules import what they need rather than maintaining parallel dicts.

The ``AIProvider`` enum, ``ModelPricing``, and ``ProviderInfo`` dataclasses
are defined in :mod:`lintro.ai.provider_enum` and
:mod:`lintro.ai.provider_info` respectively, and re-exported here for
backward compatibility.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from lintro.ai.provider_enum import AIProvider
from lintro.ai.provider_info import ModelPricing, ProviderInfo

# Re-export for backward compatibility — callers that do
# ``from lintro.ai.registry import AIProvider`` continue to work.
__all__ = [
    "AIProvider",
    "AIProviderRegistry",
    "DEFAULT_PRICING",
    "ModelPricing",
    "PROVIDERS",
    "ProviderInfo",
]

# -- Registry class --------------------------------------------------------


@dataclass(frozen=True)
class AIProviderRegistry:
    """Frozen registry of all supported AI providers.

    Access individual providers via attribute (``registry.anthropic``)
    or iterate with :meth:`items`.
    """

    anthropic: ProviderInfo
    openai: ProviderInfo
    _cached_model_pricing: dict[str, ModelPricing] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Pre-compute cached derived mappings."""
        pricing: dict[str, ModelPricing] = {}
        for _provider, info in self.items():
            pricing.update(info.models)
        object.__setattr__(self, "_cached_model_pricing", pricing)

    def items(self) -> Iterator[tuple[AIProvider, ProviderInfo]]:
        """Yield ``(AIProvider, ProviderInfo)`` pairs."""
        for provider in AIProvider:
            yield provider, getattr(self, provider.value)

    def get(self, provider: AIProvider) -> ProviderInfo:
        """Look up a provider by enum member.

        Args:
            provider: The provider to look up.

        Returns:
            ProviderInfo for the requested provider.
        """
        info: ProviderInfo = getattr(self, provider.value)
        return info

    @property
    def model_pricing(self) -> dict[str, ModelPricing]:
        """Flat mapping of every known model to its pricing."""
        return dict(self._cached_model_pricing)

    @property
    def default_models(self) -> dict[AIProvider, str]:
        """Map each provider to its default model identifier."""
        return {p: info.default_model for p, info in self.items()}

    @property
    def default_api_key_envs(self) -> dict[AIProvider, str]:
        """Map each provider to its default API-key env var."""
        return {p: info.default_api_key_env for p, info in self.items()}


# -- Singleton instance ----------------------------------------------------

PROVIDERS = AIProviderRegistry(
    anthropic=ProviderInfo(
        default_model="claude-sonnet-4-6",
        default_api_key_env="ANTHROPIC_API_KEY",
        models={
            "claude-sonnet-4-6": ModelPricing(3.00, 15.00),
            "claude-sonnet-4-20250514": ModelPricing(3.00, 15.00),
            "claude-haiku-4-5-20251001": ModelPricing(0.80, 4.00),
            "claude-opus-4-20250514": ModelPricing(15.00, 75.00),
        },
    ),
    openai=ProviderInfo(
        default_model="gpt-4o",
        default_api_key_env="OPENAI_API_KEY",
        models={
            "gpt-4o": ModelPricing(2.50, 10.00),
            "gpt-4o-mini": ModelPricing(0.15, 0.60),
            "gpt-4-turbo": ModelPricing(10.00, 30.00),
            "o1": ModelPricing(15.00, 60.00),
            "o1-mini": ModelPricing(1.10, 4.40),
        },
    ),
)

# Fallback pricing when a model is not in the registry.
DEFAULT_PRICING = ModelPricing(input_per_million=3.00, output_per_million=15.00)
