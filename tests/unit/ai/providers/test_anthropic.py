"""Tests for Anthropic AI provider."""

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
from lintro.ai.providers import anthropic as mod
from lintro.ai.providers.anthropic import AnthropicProvider


@dataclass
class _RecordingMessages:
    """Records the keyword arguments each ``messages.create`` call receives.

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


def _anthropic_response(
    *,
    text: str = "ok",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> SimpleNamespace:
    """Build a stand-in for an Anthropic messages response.

    Args:
        text: Text of the single content block.
        input_tokens: Prompt tokens the response reports.
        output_tokens: Completion tokens the response reports.

    Returns:
        SimpleNamespace: The response stand-in.
    """
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


class _FakeAnthropicError(Exception):
    """Stand-in for the SDK base ``anthropic.AnthropicError``."""


class _FakeAuthError(_FakeAnthropicError):
    """Stand-in for ``anthropic.AuthenticationError``."""


class _FakeRateLimitError(_FakeAnthropicError):
    """Stand-in for ``anthropic.RateLimitError``."""


class _FakeTimeoutError(_FakeAnthropicError):
    """Stand-in for ``anthropic.APITimeoutError``."""


@pytest.fixture
def fake_anthropic_sdk() -> Generator[SimpleNamespace]:
    """Patch the module's ``anthropic`` reference with fake error classes."""
    fake = SimpleNamespace(
        AnthropicError=_FakeAnthropicError,
        AuthenticationError=_FakeAuthError,
        RateLimitError=_FakeRateLimitError,
        APITimeoutError=_FakeTimeoutError,
    )
    with patch.object(mod, "anthropic", fake, create=True):
        yield fake


def test_map_errors_authentication(fake_anthropic_sdk: SimpleNamespace) -> None:
    """SDK AuthenticationError maps to AIAuthenticationError."""
    with pytest.raises(AIAuthenticationError):
        with AnthropicProvider._map_errors():
            raise _FakeAuthError("bad key")


def test_map_errors_rate_limit(fake_anthropic_sdk: SimpleNamespace) -> None:
    """SDK RateLimitError maps to AIRateLimitError."""
    with pytest.raises(AIRateLimitError):
        with AnthropicProvider._map_errors():
            raise _FakeRateLimitError("slow down")


def test_map_errors_timeout(fake_anthropic_sdk: SimpleNamespace) -> None:
    """SDK APITimeoutError maps to the generic AIProviderError."""
    with pytest.raises(AIProviderError):
        with AnthropicProvider._map_errors():
            raise _FakeTimeoutError("timed out")


def test_map_errors_generic_api_error(fake_anthropic_sdk: SimpleNamespace) -> None:
    """A generic SDK AnthropicError maps to AIProviderError."""
    with pytest.raises(AIProviderError):
        with AnthropicProvider._map_errors():
            raise _FakeAnthropicError("boom")


def test_map_errors_passes_through_on_success(
    fake_anthropic_sdk: SimpleNamespace,
) -> None:
    """The context manager is transparent when no error is raised."""
    with AnthropicProvider._map_errors():
        value = 21 * 2
    assert_that(value).is_equal_to(42)


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


async def test_anthropic_complete_parses_response():
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
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client

        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            result = await provider.complete("test prompt", system="be helpful")

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


async def test_anthropic_complete_multiple_text_blocks():
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
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            result = await provider.complete("prompt")

        assert_that(result.content).is_equal_to("Hello, world!")


async def test_anthropic_complete_respects_max_tokens_cap() -> None:
    """complete() uses the lower of per-call and provider-level max_tokens."""
    with patch.object(mod, "_has_anthropic", True):
        provider = AnthropicProvider(max_tokens=2048)

        messages = _RecordingMessages(response=_anthropic_response())
        provider._client = SimpleNamespace(messages=messages)

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            result = await provider.complete("prompt", max_tokens=4096)

        assert_that(result.content).is_equal_to("ok")
        assert_that(messages.calls).is_length(1)
        # The per-call 4096 is capped by the provider-level 2048.
        assert_that(messages.calls[0]["max_tokens"]).is_equal_to(2048)
