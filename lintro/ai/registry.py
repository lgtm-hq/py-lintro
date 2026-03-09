"""AI provider registry — single source of truth for provider metadata.

Consolidates model pricing, default models, and API key environment
variables into a frozen dataclass hierarchy keyed by an ``AIProvider``
StrEnum.  Every piece of provider metadata lives here; downstream
modules import what they need rather than maintaining parallel dicts.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum, auto

# -- Enums -----------------------------------------------------------------


class AIProvider(StrEnum):
    """Supported AI providers."""

    ANTHROPIC = auto()
    OPENAI = auto()


# -- Immutable dict --------------------------------------------------------


class _FrozenDict(dict[str, "ModelPricing"]):
    """Dict subclass that blocks mutation after construction.

    Inherits from ``dict`` so ``dataclasses.asdict()`` can recursively
    convert nested dataclasses, while preventing accidental mutation of
    the provider registry's model pricing data.
    """

    def __setitem__(self, key: str, value: object) -> None:  # noqa: ARG002
        """Block item assignment."""
        raise TypeError("_FrozenDict does not support item assignment")

    def __delitem__(self, key: str) -> None:  # noqa: ARG002
        """Block item deletion."""
        raise TypeError("_FrozenDict does not support item deletion")

    def update(self, *args: object, **kwargs: object) -> None:
        """Block update."""
        raise TypeError("_FrozenDict does not support update")

    def pop(self, *args: object) -> ModelPricing:
        """Block pop."""
        raise TypeError("_FrozenDict does not support pop")

    def clear(self) -> None:
        """Block clear."""
        raise TypeError("_FrozenDict does not support clear")

    def setdefault(
        self,
        key: str,
        default: ModelPricing | None = None,
    ) -> ModelPricing:
        """Block setdefault."""
        raise TypeError("_FrozenDict does not support setdefault")

    def popitem(self) -> tuple[str, ModelPricing]:
        """Block popitem."""
        raise TypeError("_FrozenDict does not support popitem")

    def __ior__(  # type: ignore[override, misc]
        self,
        other: object,  # noqa: ARG002
    ) -> _FrozenDict:
        """Block in-place union (``|=``)."""
        raise TypeError("_FrozenDict does not support |=")


# -- Data structures -------------------------------------------------------


@dataclass(frozen=True)
class ModelPricing:
    """Per-model pricing in USD per 1 million tokens."""

    input_per_million: float
    output_per_million: float


@dataclass(frozen=True)
class ProviderInfo:
    """Metadata for a single AI provider.

    Attributes:
        default_model: Model identifier used when the user omits one.
        default_api_key_env: Environment variable checked for the API key.
        models: Known models and their pricing.
    """

    default_model: str
    default_api_key_env: str
    models: Mapping[str, ModelPricing] = field(default_factory=_FrozenDict)

    def __post_init__(self) -> None:
        """Wrap models in an immutable ``_FrozenDict``.

        The frozen dataclass prevents attribute reassignment, but a plain
        dict passed at construction time would still be mutable.  Wrapping
        in ``_FrozenDict`` blocks mutation while remaining compatible with
        ``dataclasses.asdict()`` (which requires a real ``dict`` subclass).
        """
        if not isinstance(self.models, _FrozenDict):
            object.__setattr__(
                self,
                "models",
                _FrozenDict(self.models),
            )


@dataclass(frozen=True)
class AIProviderRegistry:
    """Frozen registry of all supported AI providers.

    Access individual providers via attribute (``registry.anthropic``)
    or iterate with :meth:`items`.
    """

    anthropic: ProviderInfo
    openai: ProviderInfo

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
        result: dict[str, ModelPricing] = {}
        for _provider, info in self.items():
            result.update(info.models)
        return result

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
