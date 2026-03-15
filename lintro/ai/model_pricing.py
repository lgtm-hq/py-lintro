"""Per-model pricing dataclass.

Defines the :class:`ModelPricing` frozen dataclass used by provider
metadata to track input/output costs per million tokens.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """Per-model pricing in USD per 1 million tokens."""

    input_per_million: float
    output_per_million: float
