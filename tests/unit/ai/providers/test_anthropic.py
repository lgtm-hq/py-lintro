"""Tests for Anthropic AI provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
)
from lintro.ai.providers import anthropic as mod
from lintro.ai.providers.anthropic import AnthropicProvider


def test_anthropic_provider_raises_when_sdk_missing():
    """AnthropicProvider raises AINotAvailableError if SDK missing."""
    with patch.object(mod, "_has_anthropic", False), pytest.raises(AINotAvailableError):
        AnthropicProvider()


def test_anthropic_provider_default_model():
    """AnthropicProvider uses expected default model and name."""
    with patch.object(mod, "_has_anthropic", True):
        provider = AnthropicProvider()

        assert_that(provider.model_name).is_equal_to(
            "claude-sonnet-4-6",
        )
        assert_that(provider.name).is_equal_to("anthropic")


def test_anthropic_provider_is_available_with_no_key():
    """Verify that is_available returns False when no API key is set."""
    with patch.object(mod, "_has_anthropic", True):
        provider = AnthropicProvider()
        provider._api_key_env = "NONEXISTENT_KEY_VAR"

        with patch.dict("os.environ", {}, clear=True):
            assert_that(provider.is_available()).is_false()


def test_anthropic_provider_is_available_with_key():
    """Verify that is_available returns True when a valid API key is present."""
    with patch.object(mod, "_has_anthropic", True):
        provider = AnthropicProvider()
        provider._api_key_env = "TEST_API_KEY"

        with patch.dict(
            "os.environ",
            {"TEST_API_KEY": "sk-test"},
        ):
            assert_that(provider.is_available()).is_true()


def test_anthropic_provider_get_client_no_key_raises():
    """_get_client raises AIAuthenticationError when key missing."""
    with patch.object(mod, "_has_anthropic", True):
        provider = AnthropicProvider()
        provider._api_key_env = "NONEXISTENT_KEY"

        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(AIAuthenticationError),
        ):
            provider._get_client()


def test_anthropic_complete_parses_response():
    """complete() extracts content, tokens, and cost from SDK response."""
    with patch.object(mod, "_has_anthropic", True):
        provider = AnthropicProvider()
        provider._api_key_env = "TEST_KEY"

        mock_block = MagicMock()
        mock_block.text = "Hello, world!"

        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50

        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        provider._client = mock_client

        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            result = provider.complete("test prompt", system="be helpful")

        assert_that(result.content).is_equal_to("Hello, world!")
        assert_that(result.input_tokens).is_equal_to(100)
        assert_that(result.output_tokens).is_equal_to(50)
        assert_that(result.provider).is_equal_to("anthropic")
        assert_that(result.cost_estimate).is_greater_than_or_equal_to(0.0)

        call_kwargs = mock_client.messages.create.call_args[1]
        assert_that(call_kwargs["system"]).is_equal_to("be helpful")
        assert_that(call_kwargs["messages"]).is_equal_to(
            [{"role": "user", "content": "test prompt"}],
        )


def test_anthropic_complete_multiple_text_blocks():
    """complete() concatenates multiple text blocks."""
    with patch.object(mod, "_has_anthropic", True):
        provider = AnthropicProvider()

        block1 = MagicMock()
        block1.text = "Hello, "
        block2 = MagicMock()
        block2.text = "world!"

        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5

        mock_response = MagicMock()
        mock_response.content = [block1, block2]
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        provider._client = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            result = provider.complete("prompt")

        assert_that(result.content).is_equal_to("Hello, world!")


def test_anthropic_complete_respects_max_tokens_cap():
    """complete() uses the lower of per-call and provider-level max_tokens."""
    with patch.object(mod, "_has_anthropic", True):
        provider = AnthropicProvider(max_tokens=2048)

        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        provider._client = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            provider.complete("prompt", max_tokens=4096)

        call_kwargs = mock_client.messages.create.call_args[1]
        assert_that(call_kwargs["max_tokens"]).is_equal_to(2048)
