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


def estimate_cost_with_floor(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate cost, using default pricing when a model is unpriced.

    Some providers (notably Cursor via its subscription ``agent`` CLI) do
    not expose per-token pricing, so their registry entries carry zero
    rates. Pricing such calls at zero would let them accrue nothing against
    :class:`~lintro.ai.budget.CostBudget`, turning ``ai.max_cost_usd`` into
    a no-op and allowing deep reviews to run unbounded API calls. To keep
    the budget a meaningful safety cap, fall back to :data:`DEFAULT_PRICING`
    whenever the model is unknown or priced at zero.

    Args:
        model: Model identifier (e.g., "auto", "composer-2.5").
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        float: Estimated cost in USD, always using a non-zero rate.
    """
    pricing = PROVIDERS.model_pricing.get(model)
    if pricing is None or (
        pricing.input_per_million == 0.0 and pricing.output_per_million == 0.0
    ):
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
    if cost < 0:
        cost = 0
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
