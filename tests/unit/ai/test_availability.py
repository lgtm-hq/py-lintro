"""Tests for AI availability checks."""

from __future__ import annotations

from unittest.mock import patch

import click
import pytest
from assertpy import assert_that

from lintro.ai.availability import (
    is_ai_available,
    is_provider_available,
    require_ai,
    reset_availability_cache,
)


def test_availability_available_when_anthropic_installed():
    """Verify AI is available when the anthropic module is installed."""
    reset_availability_cache()
    with patch.dict("sys.modules", {"anthropic": object()}):
        reset_availability_cache()
        result = is_ai_available()
        assert_that(result).is_true()


def test_availability_caching():
    """Subsequent calls use cached result without re-importing."""
    import sys

    reset_availability_cache()
    sys.modules.pop("anthropic", None)
    sys.modules.pop("openai", None)
    with patch(
        "importlib.import_module",
        side_effect=ImportError,
    ):
        result1 = is_ai_available()
        result2 = is_ai_available()
    assert_that(result1).is_equal_to(result2)


def test_availability_unknown_provider():
    """Verify that an unknown provider is reported as unavailable."""
    result = is_provider_available("unknown")
    assert_that(result).is_false()


def test_availability_require_raises_when_unavailable():
    """Verify require_ai raises UsageError when AI is not available."""
    with (
        patch(
            "lintro.ai.availability.is_ai_available",
            return_value=False,
        ),
        pytest.raises(click.UsageError, match="lintro\\[ai\\]"),
    ):
        require_ai()


def test_availability_require_passes_when_available():
    """Verify require_ai succeeds without error when AI is available."""
    with patch(
        "lintro.ai.availability.is_ai_available",
        return_value=True,
    ):
        require_ai()
