"""Tests for AIConfig validation — A2 max_tokens upper bound."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from pydantic import ValidationError

from lintro.ai.config import AIConfig


def test_max_tokens_default():
    """Default max_tokens is 4096."""
    config = AIConfig()
    assert_that(config.max_tokens).is_equal_to(4096)


def test_max_tokens_valid_upper_bound():
    """max_tokens at the upper bound (128000) is accepted."""
    config = AIConfig(max_tokens=128_000)
    assert_that(config.max_tokens).is_equal_to(128_000)


def test_max_tokens_exceeds_upper_bound():
    """max_tokens above 128000 raises ValidationError."""
    with pytest.raises(ValidationError, match="max_tokens"):
        AIConfig(max_tokens=200_000)


def test_max_tokens_zero_rejected():
    """max_tokens=0 raises ValidationError."""
    with pytest.raises(ValidationError, match="max_tokens"):
        AIConfig(max_tokens=0)


def test_sanitize_mode_default():
    """Default sanitize_mode is 'warn'."""
    config = AIConfig()
    assert_that(config.sanitize_mode.value).is_equal_to("warn")


def test_sanitize_mode_block():
    """sanitize_mode='block' is accepted."""
    config = AIConfig(sanitize_mode="block")  # type: ignore[arg-type]
    assert_that(config.sanitize_mode.value).is_equal_to("block")


def test_sanitize_mode_off():
    """sanitize_mode='off' is accepted."""
    config = AIConfig(sanitize_mode="off")  # type: ignore[arg-type]
    assert_that(config.sanitize_mode.value).is_equal_to("off")
