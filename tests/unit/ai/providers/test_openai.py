"""Tests for OpenAI AI provider."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.providers import openai as mod
from lintro.ai.providers.openai import OpenAIProvider


@dataclass
class _RecordingCompletions:
    """Records the keyword arguments each ``completions.create`` call receives.

    Used instead of an ``AsyncMock`` so tests assert on a list the fake really
    appended to rather than on mock call bookkeeping (#2315).

    Attributes:
        response: Object every call returns.
        calls: Keyword arguments of each call, in order.
    """

    response: Any
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> Any:
        """Record one request and return the canned response.

        Args:
            **kwargs: Request keyword arguments the provider built.

        Returns:
            Any: The canned response.
        """
        self.calls.append(kwargs)
        return self.response


def _openai_client(completions: _RecordingCompletions) -> SimpleNamespace:
    """Wrap a recording completions object in the SDK's client shape.

    Args:
        completions: The recorder to expose at ``chat.completions``.

    Returns:
        SimpleNamespace: A client stand-in the provider can call.
    """
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _openai_response(
    *,
    content: str = "response",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> SimpleNamespace:
    """Build a stand-in for an OpenAI chat-completions response.

    Args:
        content: Assistant message content.
        prompt_tokens: Prompt tokens the response reports.
        completion_tokens: Completion tokens the response reports.

    Returns:
        SimpleNamespace: The response stand-in.
    """
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


class _FakeOpenAIError(Exception):
    """Stand-in for the SDK base ``openai.OpenAIError``."""


class _FakeAuthError(_FakeOpenAIError):
    """Stand-in for ``openai.AuthenticationError``."""


class _FakeRateLimitError(_FakeOpenAIError):
    """Stand-in for ``openai.RateLimitError``."""


class _FakeTimeoutError(_FakeOpenAIError):
    """Stand-in for ``openai.APITimeoutError``."""


@pytest.fixture
def fake_openai_sdk() -> Generator[SimpleNamespace]:
    """Patch the module's ``openai`` reference with fake error classes."""
    fake = SimpleNamespace(
        OpenAIError=_FakeOpenAIError,
        AuthenticationError=_FakeAuthError,
        RateLimitError=_FakeRateLimitError,
        APITimeoutError=_FakeTimeoutError,
    )
    with patch.object(mod, "openai", fake, create=True):
        yield fake


def test_map_errors_authentication(fake_openai_sdk: SimpleNamespace) -> None:
    """SDK AuthenticationError maps to AIAuthenticationError."""
    with pytest.raises(AIAuthenticationError):
        with OpenAIProvider._map_errors():
            raise _FakeAuthError("bad key")


def test_map_errors_rate_limit(fake_openai_sdk: SimpleNamespace) -> None:
    """SDK RateLimitError maps to AIRateLimitError."""
    with pytest.raises(AIRateLimitError):
        with OpenAIProvider._map_errors():
            raise _FakeRateLimitError("slow down")


def test_map_errors_timeout(fake_openai_sdk: SimpleNamespace) -> None:
    """SDK APITimeoutError maps to the generic AIProviderError."""
    with pytest.raises(AIProviderError):
        with OpenAIProvider._map_errors():
            raise _FakeTimeoutError("timed out")


def test_map_errors_generic_api_error(fake_openai_sdk: SimpleNamespace) -> None:
    """A generic SDK OpenAIError maps to AIProviderError."""
    with pytest.raises(AIProviderError):
        with OpenAIProvider._map_errors():
            raise _FakeOpenAIError("boom")


def test_map_errors_passes_through_on_success(fake_openai_sdk: SimpleNamespace) -> None:
    """The context manager is transparent when no error is raised."""
    with OpenAIProvider._map_errors():
        value = 21 * 2
    assert_that(value).is_equal_to(42)


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


async def test_openai_complete_parses_response():
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
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client

        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            result = await provider.complete(
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


async def test_openai_complete_without_system_prompt() -> None:
    """complete() omits system message when system is None."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider()

        completions = _RecordingCompletions(response=_openai_response())
        provider._client = _openai_client(completions)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = await provider.complete("prompt")

        assert_that(result.content).is_equal_to("response")
        assert_that(completions.calls).is_length(1)
        assert_that(completions.calls[0]["messages"]).is_equal_to(
            [{"role": "user", "content": "prompt"}],
        )


async def test_openai_complete_handles_none_usage():
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
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = await provider.complete("prompt")

        assert_that(result.input_tokens).is_equal_to(0)
        assert_that(result.output_tokens).is_equal_to(0)


async def test_openai_complete_respects_max_tokens_cap() -> None:
    """complete() uses the lower of per-call and provider-level max_tokens."""
    with patch.object(mod, "_has_openai", True):
        provider = OpenAIProvider(max_tokens=2048)

        completions = _RecordingCompletions(response=_openai_response(content="ok"))
        provider._client = _openai_client(completions)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = await provider.complete("prompt", max_tokens=4096)

        assert_that(result.content).is_equal_to("ok")
        assert_that(completions.calls).is_length(1)
        # The per-call 4096 is capped by the provider-level 2048.
        assert_that(completions.calls[0]["max_tokens"]).is_equal_to(2048)
