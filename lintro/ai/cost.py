"""Token counting and cost estimation for AI operations.

Provides per-model pricing and utility functions for estimating
and formatting costs of AI API calls.
"""

from __future__ import annotations

# Pricing per 1M tokens (input, output) in USD.
# Last updated: 2025-05 — verify at provider pricing pages before relying on these.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic models
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-haiku-3-5-20241022": (0.80, 4.00),
    "claude-opus-4-20250514": (15.00, 75.00),
    # OpenAI models
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "o1": (15.00, 60.00),
    "o1-mini": (1.10, 4.40),
}

# Default pricing when model is unknown
DEFAULT_PRICING: tuple[float, float] = (3.00, 15.00)


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate the cost of an AI API call.

    Args:
        model: Model identifier (e.g., "claude-sonnet-4-20250514").
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        float: Estimated cost in USD.
    """
    input_price, output_price = MODEL_PRICING.get(model, DEFAULT_PRICING)

    input_cost = (input_tokens / 1_000_000) * input_price
    output_cost = (output_tokens / 1_000_000) * output_price

    return input_cost + output_cost


def format_cost(cost: float) -> str:
    """Format a cost value for display.

    Args:
        cost: Cost in USD.

    Returns:
        str: Formatted cost string (e.g., "$0.003", "<$0.001").
    """
    if cost < 0.001:
        return "<$0.001"
    return f"${cost:.3f}"


def format_token_count(tokens: int) -> str:
    """Format a token count for display.

    Args:
        tokens: Number of tokens.

    Returns:
        str: Formatted token count (e.g., "~1,230").
    """
    return f"~{tokens:,}"
