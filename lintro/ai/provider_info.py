"""Provider metadata dataclasses.

Houses the frozen :class:`ModelPricing` and :class:`ProviderInfo`
dataclasses used by the provider registry.  Separated from
:mod:`lintro.ai.registry` so that consumers needing only the data
shapes can import them without depending on the singleton instance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


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
            Typed as ``Mapping`` to signal read-only intent; the frozen
            dataclass prevents reassignment of the attribute itself.
    """

    default_model: str
    default_api_key_env: str
    models: Mapping[str, ModelPricing] = field(default_factory=dict)
