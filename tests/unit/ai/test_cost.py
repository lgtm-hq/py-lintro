"""Tests for AI cost estimation."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.cost import (
    MODEL_PRICING,
    estimate_cost,
    format_cost,
    format_token_count,
)


class TestEstimateCost:
    """Tests for estimate_cost function."""

    def test_known_model(self):
        cost = estimate_cost("gpt-4o", 1000, 500)
        # gpt-4o: $2.50/M input, $10.00/M output
        expected = (1000 / 1_000_000) * 2.50 + (500 / 1_000_000) * 10.00
        assert_that(cost).is_close_to(expected, 1e-10)

    def test_unknown_model_uses_default(self):
        cost = estimate_cost("unknown-model", 1000, 500)
        # Default: $3.00/M input, $15.00/M output
        expected = (1000 / 1_000_000) * 3.00 + (500 / 1_000_000) * 15.00
        assert_that(cost).is_close_to(expected, 1e-10)

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4o", 0, 0)
        assert_that(cost).is_equal_to(0.0)

    def test_large_token_count(self):
        cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
        assert_that(cost).is_greater_than(0)

    @pytest.mark.parametrize(
        "model",
        list(MODEL_PRICING.keys()),
    )
    def test_all_known_models_have_pricing(self, model):
        cost = estimate_cost(model, 1000, 1000)
        assert_that(cost).is_greater_than(0)


class TestFormatCost:
    """Tests for format_cost function."""

    def test_small_cost(self):
        result = format_cost(0.0001)
        assert_that(result).is_equal_to("<$0.001")

    def test_normal_cost(self):
        result = format_cost(0.005)
        assert_that(result).is_equal_to("$0.005")

    def test_larger_cost(self):
        result = format_cost(1.234)
        assert_that(result).is_equal_to("$1.234")


class TestFormatTokenCount:
    """Tests for format_token_count function."""

    def test_small_count(self):
        result = format_token_count(100)
        assert_that(result).is_equal_to("~100")

    def test_large_count(self):
        result = format_token_count(1234567)
        assert_that(result).is_equal_to("~1,234,567")
