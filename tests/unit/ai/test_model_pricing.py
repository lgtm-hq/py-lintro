"""Tests for model context window helpers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.model_pricing import (
    DEFAULT_CONTEXT_WINDOW,
    calculate_available_diff_tokens,
    get_context_window,
)


def test_get_context_window_returns_known_model_size() -> None:
    """Known models return configured context window sizes."""
    assert_that(
        get_context_window(model="claude-sonnet-4-20250514"),
    ).is_equal_to(200_000)


def test_get_context_window_uses_override_when_provided() -> None:
    """Explicit override takes precedence over model defaults."""
    assert_that(
        get_context_window(model="unknown-model", override=64_000),
    ).is_equal_to(64_000)


def test_get_context_window_falls_back_to_default() -> None:
    """Unknown models fall back to default context window."""
    assert_that(
        get_context_window(model="unknown-model"),
    ).is_equal_to(DEFAULT_CONTEXT_WINDOW)


def test_calculate_available_diff_tokens_subtracts_overhead() -> None:
    """Available diff tokens subtract prompt overhead from context window."""
    available = calculate_available_diff_tokens(
        context_window=200_000,
        prompt_overhead=20_000,
    )

    assert_that(available).is_equal_to(180_000)
