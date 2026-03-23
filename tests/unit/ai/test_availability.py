"""Tests for AI availability checks."""

from __future__ import annotations

import builtins
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

_real_import = builtins.__import__


def test_availability_available_when_anthropic_installed():
    """Verify AI is available when the anthropic module is installed."""
    with patch.dict("sys.modules", {"anthropic": object()}):
        reset_availability_cache()
        result = is_ai_available()
        assert_that(result).is_true()


def test_availability_caching():
    """Subsequent calls use the cached result without re-importing."""
    import_calls: list[str] = []

    def _tracking_import(name, *args, **kwargs):
        if name in ("anthropic", "openai"):
            import_calls.append(name)
        return _real_import(name, *args, **kwargs)

    reset_availability_cache()
    with (
        patch.dict("sys.modules", {"anthropic": object()}),
        patch("builtins.__import__", side_effect=_tracking_import),
    ):
        result1 = is_ai_available()

    import_calls.clear()

    # Second call should return cached True without any import attempt.
    with patch("builtins.__import__", side_effect=_tracking_import):
        result2 = is_ai_available()

    assert_that(result1).is_true()
    assert_that(result2).is_true()
    assert_that(import_calls).is_empty()


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
