"""Per-model pricing dataclass and context window registry.

Defines the :class:`ModelPricing` frozen dataclass used by provider
metadata to track input/output costs per million tokens, plus context
window sizes for review token budgeting.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "MODEL_CONTEXT_WINDOWS",
    "ModelPricing",
    "calculate_available_diff_tokens",
    "get_context_window",
]

DEFAULT_CONTEXT_WINDOW = 128_000

MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-opus-4-20250514": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "o1": 200_000,
    "auto": 200_000,
    "composer-2.5-fast": 200_000,
    "composer-2.5": 200_000,
    "o1-mini": 128_000,
}


@dataclass(frozen=True)
class ModelPricing:
    """Per-model pricing in USD per 1 million tokens."""

    input_per_million: float
    output_per_million: float


def get_context_window(*, model: str, override: int | None = None) -> int:
    """Return the context window size for a model.

    Args:
        model: Model identifier.
        override: Optional explicit override from CLI/config.

    Returns:
        Context window size in tokens.
    """
    if override is not None and override > 0:
        return override
    return MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)


def calculate_available_diff_tokens(
    *,
    context_window: int,
    prompt_overhead: int,
) -> int:
    """Calculate token budget available for diff content.

    Args:
        context_window: Total model context window.
        prompt_overhead: Estimated tokens for system/user prompt overhead.

    Returns:
        Remaining token budget for diff content (minimum zero).

    Raises:
        ValueError: If ``context_window`` is not positive or ``prompt_overhead``
            is negative.
    """
    if context_window <= 0:
        raise ValueError(f"context_window must be positive, got {context_window}")
    if prompt_overhead < 0:
        raise ValueError(f"prompt_overhead must be non-negative, got {prompt_overhead}")
    return max(context_window - prompt_overhead, 0)
