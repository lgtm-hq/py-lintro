"""Tests for AI cost estimation."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.cost import (
    MODEL_PRICING,
    estimate_cost,
    format_cost,
    format_token_count,
)


def test_cost_known_model():
    """Verify cost estimation uses correct pricing for a known model."""
    cost = estimate_cost("gpt-4o", 1000, 500)
    expected = (1000 / 1_000_000) * 2.50 + (500 / 1_000_000) * 10.00
    assert_that(cost).is_close_to(expected, 1e-10)


def test_cost_unknown_model_uses_default():
    """Verify cost estimation falls back to default pricing for unknown models."""
    cost = estimate_cost("unknown-model", 1000, 500)
    expected = (1000 / 1_000_000) * 3.00 + (500 / 1_000_000) * 15.00
    assert_that(cost).is_close_to(expected, 1e-10)


@patch("lintro.ai.cost.logger")
def test_cost_unknown_model_logs_debug(mock_logger):
    """Verify a debug message is logged for unknown model default pricing."""
    estimate_cost("totally-unknown-model", 100, 50)
    mock_logger.debug.assert_called_once()
    call_args = mock_logger.debug.call_args[0][0]
    assert_that(call_args).contains("totally-unknown-model")
    assert_that(call_args).contains("default pricing")


@patch("lintro.ai.cost.logger")
def test_cost_known_model_does_not_log(mock_logger):
    """Verify no debug message is logged when pricing is found for a known model."""
    estimate_cost("gpt-4o", 100, 50)
    mock_logger.debug.assert_not_called()


def test_cost_zero_tokens():
    """Verify cost is zero when both input and output token counts are zero."""
    cost = estimate_cost("gpt-4o", 0, 0)
    assert_that(cost).is_equal_to(0.0)


def test_cost_large_token_count():
    """Verify cost estimation handles large token counts correctly."""
    cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
    assert_that(cost).is_greater_than(0)


@pytest.mark.parametrize("model", list(MODEL_PRICING.keys()))
def test_cost_all_known_models_have_pricing(model):
    """Verify every model in MODEL_PRICING produces a positive cost estimate."""
    cost = estimate_cost(model, 1000, 1000)
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
