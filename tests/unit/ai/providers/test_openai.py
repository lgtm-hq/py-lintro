"""Tests for OpenAI AI provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
)
from lintro.ai.providers import openai as mod
from lintro.ai.providers.openai import OpenAIProvider


def test_openai_provider_raises_when_sdk_missing():
    """Verify that OpenAIProvider raises AINotAvailableError when the SDK is missing."""
    with (
        patch.object(mod, "_has_openai", False),
        pytest.raises(AINotAvailableError),
    ):
        OpenAIProvider()


def test_openai_provider_default_model():
    """Verify that OpenAIProvider uses the expected default model and provider name."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()

        assert_that(provider.model_name).is_equal_to("gpt-4o")
        assert_that(provider.name).is_equal_to("openai")


def test_openai_provider_is_available_with_no_key():
    """Verify that is_available returns False when no API key is set."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()
        provider._api_key_env = "NONEXISTENT_KEY_VAR"

        with patch.dict("os.environ", {}, clear=True):
            assert_that(provider.is_available()).is_false()


def test_openai_provider_is_available_with_key():
    """Verify that is_available returns True when a valid API key is present."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()
        provider._api_key_env = "TEST_API_KEY"

        with patch.dict(
            "os.environ",
            {"TEST_API_KEY": "sk-test"},
        ):
            assert_that(provider.is_available()).is_true()


def test_openai_provider_get_client_no_key_raises():
    """_get_client raises AIAuthenticationError when key missing."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()
        provider._api_key_env = "NONEXISTENT_KEY"

        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(AIAuthenticationError),
        ):
            provider._get_client()


def test_openai_complete_parses_response():
    """complete() extracts content, tokens, and cost from SDK response."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()
        provider._api_key_env = "TEST_KEY"

        mock_message = MagicMock()
        mock_message.content = "Hello from GPT!"

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 200
        mock_usage.completion_tokens = 80

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        provider._client = mock_client

        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            result = provider.complete(
                "test prompt",
                system="be helpful",
            )

        assert_that(result.content).is_equal_to("Hello from GPT!")
        assert_that(result.input_tokens).is_equal_to(200)
        assert_that(result.output_tokens).is_equal_to(80)
        assert_that(result.provider).is_equal_to("openai")
        assert_that(result.cost_estimate).is_greater_than_or_equal_to(0.0)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert_that(call_kwargs["messages"]).is_equal_to(
            [
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "test prompt"},
            ],
        )


def test_openai_complete_without_system_prompt():
    """complete() omits system message when system is None."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()

        mock_message = MagicMock()
        mock_message.content = "response"

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        provider._client = mock_client

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            provider.complete("prompt")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert_that(call_kwargs["messages"]).is_equal_to(
            [{"role": "user", "content": "prompt"}],
        )


def test_openai_complete_handles_none_usage():
    """complete() handles None usage gracefully (tokens default to 0)."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()

        mock_message = MagicMock()
        mock_message.content = "response"

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        provider._client = mock_client

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = provider.complete("prompt")

        assert_that(result.input_tokens).is_equal_to(0)
        assert_that(result.output_tokens).is_equal_to(0)


def test_openai_complete_respects_max_tokens_cap():
    """complete() uses the lower of per-call and provider-level max_tokens."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider(max_tokens=2048)

        mock_message = MagicMock()
        mock_message.content = "ok"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        provider._client = mock_client

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            provider.complete("prompt", max_tokens=4096)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert_that(call_kwargs["max_tokens"]).is_equal_to(2048)
