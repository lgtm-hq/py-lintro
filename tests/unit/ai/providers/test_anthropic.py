"""Tests for Anthropic AI provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
)


class TestAnthropicProvider:
    """Tests for AnthropicProvider."""

    def test_raises_when_sdk_missing(self):
        with patch.dict("sys.modules", {"anthropic": None}):
            # Force re-import to pick up the mocked module

            from lintro.ai.providers import anthropic as mod

            # Temporarily set anthropic to None to simulate missing
            original = mod.anthropic
            mod.anthropic = None
            try:
                with pytest.raises(AINotAvailableError):
                    mod.AnthropicProvider()
            finally:
                mod.anthropic = original

    def test_default_model(self):
        mock_anthropic = MagicMock()
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from lintro.ai.providers.anthropic import AnthropicProvider

            # Ensure anthropic module is not None
            with patch.object(
                AnthropicProvider,
                "__init__",
                lambda self, **kw: None,
            ):
                provider = AnthropicProvider()
                provider._model = "claude-sonnet-4-20250514"
                provider._api_key_env = "ANTHROPIC_API_KEY"
                provider._max_tokens = 4096
                provider._client = None

                assert_that(provider.model_name).is_equal_to(
                    "claude-sonnet-4-20250514",
                )
                assert_that(provider.name).is_equal_to("anthropic")

    def test_is_available_with_no_key(self):
        from lintro.ai.providers.anthropic import AnthropicProvider

        with patch.object(
            AnthropicProvider,
            "__init__",
            lambda self, **kw: None,
        ):
            provider = AnthropicProvider()
            provider._api_key_env = "NONEXISTENT_KEY_VAR"
            provider._client = None

            # Set the module-level anthropic to a mock
            import lintro.ai.providers.anthropic as mod

            original = mod.anthropic
            mod.anthropic = MagicMock()
            try:
                with patch.dict(
                    "os.environ",
                    {},
                    clear=True,
                ):
                    assert_that(provider.is_available()).is_false()
            finally:
                mod.anthropic = original

    def test_is_available_with_key(self):
        from lintro.ai.providers.anthropic import AnthropicProvider

        with patch.object(
            AnthropicProvider,
            "__init__",
            lambda self, **kw: None,
        ):
            provider = AnthropicProvider()
            provider._api_key_env = "TEST_API_KEY"
            provider._client = None

            import lintro.ai.providers.anthropic as mod

            original = mod.anthropic
            mod.anthropic = MagicMock()
            try:
                with patch.dict(
                    "os.environ",
                    {"TEST_API_KEY": "sk-test"},
                ):
                    assert_that(provider.is_available()).is_true()
            finally:
                mod.anthropic = original

    def test_get_client_no_key_raises(self):
        from lintro.ai.providers.anthropic import AnthropicProvider

        with patch.object(
            AnthropicProvider,
            "__init__",
            lambda self, **kw: None,
        ):
            provider = AnthropicProvider()
            provider._api_key_env = "NONEXISTENT_KEY"
            provider._client = None

            with patch.dict("os.environ", {}, clear=True):
                with pytest.raises(AIAuthenticationError):
                    provider._get_client()
