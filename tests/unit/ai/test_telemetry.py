"""Tests for the AI session telemetry collector."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.telemetry import AITelemetry

# -- Defaults ----------------------------------------------------------------


def test_defaults_are_zero() -> None:
    """A fresh AITelemetry has all counters at zero."""
    t = AITelemetry()
    assert_that(t.total_api_calls).is_equal_to(0)
    assert_that(t.total_input_tokens).is_equal_to(0)
    assert_that(t.total_output_tokens).is_equal_to(0)
    assert_that(t.total_cost_usd).is_equal_to(0.0)
    assert_that(t.total_latency_ms).is_equal_to(0.0)
    assert_that(t.successful_fixes).is_equal_to(0)
    assert_that(t.failed_fixes).is_equal_to(0)


# -- record_call -------------------------------------------------------------


def test_record_call_increments_all_fields() -> None:
    """A single record_call increments api_calls and accumulates metrics."""
    t = AITelemetry()
    t.record_call(input_tokens=100, output_tokens=50, cost=0.003, latency_ms=250.0)

    assert_that(t.total_api_calls).is_equal_to(1)
    assert_that(t.total_input_tokens).is_equal_to(100)
    assert_that(t.total_output_tokens).is_equal_to(50)
    assert_that(t.total_cost_usd).is_equal_to(0.003)
    assert_that(t.total_latency_ms).is_equal_to(250.0)


def test_multiple_calls_accumulate() -> None:
    """Multiple record_call invocations accumulate correctly."""
    t = AITelemetry()
    t.record_call(input_tokens=100, output_tokens=50, cost=0.003, latency_ms=200.0)
    t.record_call(input_tokens=200, output_tokens=80, cost=0.005, latency_ms=300.0)

    assert_that(t.total_api_calls).is_equal_to(2)
    assert_that(t.total_input_tokens).is_equal_to(300)
    assert_that(t.total_output_tokens).is_equal_to(130)
    assert_that(t.total_cost_usd).is_close_to(0.008, tolerance=1e-9)
    assert_that(t.total_latency_ms).is_equal_to(500.0)


# -- to_dict -----------------------------------------------------------------


def test_to_dict_returns_correct_dict() -> None:
    """to_dict returns a dictionary with all expected keys and values."""
    t = AITelemetry()
    t.record_call(input_tokens=100, output_tokens=50, cost=0.003, latency_ms=250.5)
    t.successful_fixes = 3
    t.failed_fixes = 1

    d = t.to_dict()
    assert_that(d).is_equal_to(
        {
            "api_calls": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": 0.003,
            "latency_ms": 250.5,
            "successful_fixes": 3,
            "failed_fixes": 1,
        },
    )


def test_to_dict_rounds_cost() -> None:
    """to_dict rounds cost_usd to 6 decimal places."""
    t = AITelemetry()
    t.record_call(input_tokens=10, output_tokens=5, cost=0.0000001234, latency_ms=10.0)

    d = t.to_dict()
    assert_that(d["cost_usd"]).is_equal_to(0.0)


def test_to_dict_rounds_latency() -> None:
    """to_dict rounds latency_ms to 1 decimal place."""
    t = AITelemetry()
    t.record_call(input_tokens=10, output_tokens=5, cost=0.001, latency_ms=123.456)

    d = t.to_dict()
    assert_that(d["latency_ms"]).is_equal_to(123.5)
