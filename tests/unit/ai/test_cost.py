"""Tests for AI cost estimation."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.cost import (
    estimate_cost,
    estimate_cost_with_floor,
    format_cost,
    format_token_count,
)
from lintro.ai.registry import DEFAULT_PRICING, PROVIDERS, ModelPricing


@pytest.fixture
def debug_messages() -> Iterator[list[str]]:
    """Capture loguru DEBUG records emitted during the test.

    Yields:
        list[str]: A list that accumulates formatted debug messages.
    """
    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="DEBUG",
    )
    try:
        yield messages
    finally:
        logger.remove(handler_id)


def test_cost_known_model():
    """Verify cost estimation uses correct pricing for a known model."""
    pricing = PROVIDERS.model_pricing["gpt-4o"]
    cost = estimate_cost("gpt-4o", 1000, 500)
    expected = (1000 / 1_000_000) * pricing.input_per_million + (
        500 / 1_000_000
    ) * pricing.output_per_million
    assert_that(cost).is_close_to(expected, 1e-10)


def test_estimate_cost_with_floor_partial_zero_uses_default():
    """Verify partial zero pricing falls back to default rates."""
    partial_zero = ModelPricing(input_per_million=3.0, output_per_million=0.0)
    with patch.dict(PROVIDERS.model_pricing, {"partial-zero": partial_zero}):
        cost = estimate_cost_with_floor("partial-zero", 1_000_000, 1_000_000)
    expected = DEFAULT_PRICING.input_per_million + DEFAULT_PRICING.output_per_million
    assert_that(cost).is_close_to(expected, 1e-10)


def test_cost_unknown_model_uses_default():
    """Verify cost estimation falls back to default pricing for unknown models."""
    cost = estimate_cost("unknown-model", 1000, 500)
    expected = (1000 / 1_000_000) * DEFAULT_PRICING.input_per_million + (
        500 / 1_000_000
    ) * DEFAULT_PRICING.output_per_million
    assert_that(cost).is_close_to(expected, 1e-10)


def test_cost_unknown_model_logs_debug(debug_messages: list[str]) -> None:
    """An unknown model logs the default-pricing fallback it applied.

    Args:
        debug_messages: Loguru DEBUG records captured during the test.
    """
    cost = estimate_cost("totally-unknown-model", 100, 50)

    expected = (100 / 1_000_000) * DEFAULT_PRICING.input_per_million + (
        50 / 1_000_000
    ) * DEFAULT_PRICING.output_per_million
    assert_that(cost).is_close_to(expected, 1e-10)
    transcript = "\n".join(debug_messages)
    assert_that(transcript).contains("totally-unknown-model")
    assert_that(transcript).contains("default pricing")


def test_cost_known_model_does_not_log(debug_messages: list[str]) -> None:
    """A model with registered pricing logs no default-pricing fallback notice.

    Args:
        debug_messages: Loguru DEBUG records captured during the test.
    """
    pricing = PROVIDERS.model_pricing["gpt-4o"]
    cost = estimate_cost("gpt-4o", 100, 50)

    expected = (100 / 1_000_000) * pricing.input_per_million + (
        50 / 1_000_000
    ) * pricing.output_per_million
    assert_that(cost).is_close_to(expected, 1e-10)
    assert_that("\n".join(debug_messages)).does_not_contain("default pricing")


def test_cost_zero_tokens():
    """Verify cost is zero when both input and output token counts are zero."""
    cost = estimate_cost("gpt-4o", 0, 0)
    assert_that(cost).is_equal_to(0.0)


def test_cost_large_token_count():
    """Verify cost estimation handles large token counts correctly."""
    cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
    assert_that(cost).is_greater_than(0)


@pytest.mark.parametrize("model", list(PROVIDERS.model_pricing.keys()))
def test_cost_all_known_models_have_pricing(model: str) -> None:
    """Verify every registered model produces a known cost estimate."""
    pricing = PROVIDERS.model_pricing[model]
    cost = estimate_cost(model, 1000, 1000)
    if pricing.input_per_million == 0 and pricing.output_per_million == 0:
        assert_that(cost).is_equal_to(0.0)
    else:
        assert_that(cost).is_greater_than(0)


def test_cost_format_small():
    """Verify very small costs are formatted as less-than-threshold."""
    result = format_cost(0.0001)
    assert_that(result).is_equal_to("<$0.001")


def test_cost_format_normal():
    """Verify normal cost values are formatted with dollar sign and three decimals."""
    result = format_cost(0.005)
    assert_that(result).is_equal_to("$0.005")


def test_cost_format_larger():
    """Verify larger cost values are formatted correctly with dollar sign."""
    result = format_cost(1.234)
    assert_that(result).is_equal_to("$1.234")


def test_cost_format_token_count_small():
    """Verify small token counts are formatted with a tilde prefix."""
    result = format_token_count(100)
    assert_that(result).is_equal_to("~100")


def test_cost_format_token_count_large():
    """Verify large token counts are formatted with comma separators."""
    result = format_token_count(1234567)
    assert_that(result).is_equal_to("~1,234,567")
