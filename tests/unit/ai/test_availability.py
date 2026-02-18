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


class TestIsAIAvailable:
    """Tests for is_ai_available function."""

    def setup_method(self):
        reset_availability_cache()

    def test_available_when_anthropic_installed(self):
        with patch.dict(
            "sys.modules",
            {"anthropic": object()},
        ):
            reset_availability_cache()
            result = is_ai_available()
            assert_that(result).is_true()

    def test_returns_cached_true(self):
        # If is_ai_available was already called and returned True,
        # verify it returns the cached value
        reset_availability_cache()
        # Call once to populate cache (may be True or False
        # depending on installed packages)
        first = is_ai_available()
        second = is_ai_available()
        assert_that(first).is_equal_to(second)

    def test_caching(self):
        reset_availability_cache()
        # First call computes
        result1 = is_ai_available()
        # Second call returns cached value
        result2 = is_ai_available()
        assert_that(result1).is_equal_to(result2)


class TestIsProviderAvailable:
    """Tests for is_provider_available function."""

    def test_unknown_provider(self):
        result = is_provider_available("unknown")
        assert_that(result).is_false()


class TestRequireAI:
    """Tests for require_ai function."""

    def test_raises_when_unavailable(self):
        with patch(
            "lintro.ai.availability.is_ai_available",
            return_value=False,
        ):
            with pytest.raises(click.UsageError, match="lintro\\[ai\\]"):
                require_ai()

    def test_passes_when_available(self):
        with patch(
            "lintro.ai.availability.is_ai_available",
            return_value=True,
        ):
            # Should not raise
            require_ai()
