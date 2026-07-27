"""Tests for call_ai unified invocation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIError, AIProviderError
from lintro.ai.invoke import call_ai
from lintro.ai.providers.response import AIResponse


def _response(*, cost: float = 0.01) -> AIResponse:
    """Build a stub provider response.

    Args:
        cost: Cost estimate recorded on the response.

    Returns:
        A populated ``AIResponse``.
    """
    return AIResponse(
        content='{"ok": true}',
        model="test-model",
        input_tokens=10,
        output_tokens=5,
        cost_estimate=cost,
        provider="anthropic",
    )


async def test_call_ai_returns_response_and_records_budget() -> None:
    """call_ai records cost against the session budget."""
    provider = MagicMock()
    provider.complete = AsyncMock()
    provider.complete.return_value = _response(cost=0.02)
    config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        max_retries=0,
    )
    budget = CostBudget(max_cost_usd=1.0)

    response = await call_ai(
        provider=provider,
        ai_config=config,
        user_prompt="hello",
        system_prompt="system",
        budget=budget,
    )

    assert_that(response.cost_estimate).is_equal_to(0.02)
    assert_that(budget.spent).is_equal_to(0.02)


async def test_call_ai_retries_on_provider_error() -> None:
    """Transient provider errors are retried according to config."""
    provider = MagicMock()
    provider.complete = AsyncMock()
    provider.complete.side_effect = [
        AIProviderError("temporary"),
        _response(),
    ]
    config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        max_retries=1,
        retry_base_delay=0.1,
        retry_max_delay=1.0,
    )

    with patch("lintro.ai.retry.asyncio.sleep"):
        response = await call_ai(
            provider=provider,
            ai_config=config,
            user_prompt="hello",
            system_prompt=None,
            budget=None,
        )

    assert_that(response.content).contains("ok")
    assert_that(provider.complete.call_count).is_equal_to(2)


async def test_call_ai_returns_response_when_single_call_exceeds_budget() -> None:
    """A completed call is returned even when it pushes spent over the limit."""
    provider = MagicMock()
    provider.complete = AsyncMock()
    provider.complete.return_value = _response(cost=0.50)
    config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        max_retries=0,
    )
    budget = CostBudget(max_cost_usd=0.10)

    response = await call_ai(
        provider=provider,
        ai_config=config,
        user_prompt="hello",
        system_prompt=None,
        budget=budget,
    )

    assert_that(response.cost_estimate).is_equal_to(0.50)
    assert_that(budget.spent).is_equal_to(0.50)


async def test_call_ai_raises_when_budget_already_exceeded() -> None:
    """Subsequent calls fail once the budget ceiling has been reached."""
    provider = MagicMock()
    provider.complete = AsyncMock()
    provider.complete.return_value = _response(cost=0.50)
    config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        max_retries=0,
    )
    budget = CostBudget(max_cost_usd=0.10)
    budget.record(0.10)

    with pytest.raises(AIError, match="budget exceeded"):
        await call_ai(
            provider=provider,
            ai_config=config,
            user_prompt="hello",
            system_prompt=None,
            budget=budget,
        )
