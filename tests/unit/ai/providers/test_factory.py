"""Tests for AI provider factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.providers import get_provider


class TestGetProvider:
    """Tests for get_provider factory."""

    def test_anthropic_provider(self):
        config = AIConfig(provider="anthropic")
        mock_anthropic = MagicMock()
        with patch(
            "lintro.ai.providers.anthropic.anthropic",
            mock_anthropic,
        ):
            provider = get_provider(config)
            assert_that(provider.name).is_equal_to("anthropic")

    def test_openai_provider(self):
        config = AIConfig(provider="openai")
        mock_openai = MagicMock()
        with patch(
            "lintro.ai.providers.openai.openai",
            mock_openai,
        ):
            provider = get_provider(config)
            assert_that(provider.name).is_equal_to("openai")

    def test_unknown_provider_raises(self):
        config = AIConfig(provider="unknown")
        with pytest.raises(ValueError, match="Unknown AI provider"):
            get_provider(config)

    def test_case_insensitive(self):
        config = AIConfig(provider="Anthropic")
        mock_anthropic = MagicMock()
        with patch(
            "lintro.ai.providers.anthropic.anthropic",
            mock_anthropic,
        ):
            provider = get_provider(config)
            assert_that(provider.name).is_equal_to("anthropic")

    def test_passes_model(self):
        config = AIConfig(
            provider="anthropic",
            model="claude-opus-4-20250514",
        )
        mock_anthropic = MagicMock()
        with patch(
            "lintro.ai.providers.anthropic.anthropic",
            mock_anthropic,
        ):
            provider = get_provider(config)
            assert_that(provider.model_name).is_equal_to(
                "claude-opus-4-20250514",
            )
