"""Token counting and cost estimation for AI operations.

Provides per-model pricing and utility functions for estimating
and formatting costs of AI API calls.
"""

from __future__ import annotations

from loguru import logger

from lintro.ai.registry import DEFAULT_PRICING, PROVIDERS


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
    pricing = PROVIDERS.model_pricing.get(model)
    if pricing is None:
        logger.debug(f"Unknown model {model!r}, using default pricing")
        pricing = DEFAULT_PRICING

    input_cost = (input_tokens / 1_000_000) * pricing.input_per_million
    output_cost = (output_tokens / 1_000_000) * pricing.output_per_million

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
