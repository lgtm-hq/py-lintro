"""Tests for AI provider factory."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers import _implemented_provider_list, get_provider
from lintro.ai.providers import anthropic as anthropic_mod
from lintro.ai.providers import openai as openai_mod


def test_get_provider_anthropic():
    """Verify that get_provider returns an Anthropic provider when configured."""
    config = AIConfig(provider="anthropic")  # type: ignore[arg-type]  # Pydantic coerces str
    with patch.object(anthropic_mod, "_has_anthropic", True):
        provider = get_provider(config)
        assert_that(provider.name).is_equal_to("anthropic")


def test_get_provider_openai():
    """Verify that get_provider returns an OpenAI provider when configured."""
    config = AIConfig(provider="openai")  # type: ignore[arg-type]  # Pydantic coerces str
    with patch.object(openai_mod, "_has_openai", True):
        provider = get_provider(config)
        assert_that(provider.name).is_equal_to("openai")


def test_get_provider_unknown_raises():
    """Verify that get_provider raises ValueError for an unknown provider name."""
    # Bypass Pydantic validation to test the factory's own guard.
    config = AIConfig.model_construct(provider="unknown")
    with pytest.raises(ValueError, match="Unknown AI provider"):
        get_provider(config)


def test_implemented_provider_list_is_alphabetical() -> None:
    """The unimplemented-provider error lists names alphabetically."""
    classes = {
        AIProvider.ANTHROPIC: object(),
        AIProvider.OPENAI: object(),
        AIProvider.CURSOR: object(),
    }
    assert_that(
        _implemented_provider_list(provider_classes=classes),
    ).is_equal_to("anthropic, cursor, openai")


def test_get_provider_requires_explicit_provider() -> None:
    """An enabled-but-unset provider fails with the three-way migration path."""
    from lintro.ai.exceptions import AIProviderRequiredError

    config = AIConfig(enabled=True, lint=True, review=False)
    with pytest.raises(
        AIProviderRequiredError,
        match="ai.provider is required",
    ) as exc_info:
        get_provider(config)
    message = str(exc_info.value)
    assert_that(message).contains("`ai.provider` in config")
    assert_that(message).contains("LINTRO_AI_PROVIDER")
    assert_that(message).contains("--provider")
    assert_that(message).contains("anthropic, cursor, openai")


def test_get_provider_case_insensitive():
    """Verify that get_provider handles provider names case-insensitively."""
    # Bypass Pydantic Literal validation to test the factory lowercases.
    config = AIConfig.model_construct(
        provider="Anthropic",
        model=None,
        api_key_env=None,
        max_tokens=4096,
    )
    with patch.object(anthropic_mod, "_has_anthropic", True):
        provider = get_provider(config)
        assert_that(provider.name).is_equal_to("anthropic")


def test_get_provider_passes_model():
    """Verify that get_provider forwards the configured model to the provider."""
    config = AIConfig(
        provider="anthropic",  # type: ignore[arg-type]  # Pydantic coerces str
        model="claude-opus-4-20250514",
    )
    with patch.object(anthropic_mod, "_has_anthropic", True):
        provider = get_provider(config)
        assert_that(provider.model_name).is_equal_to(
            "claude-opus-4-20250514",
        )


def test_get_provider_cursor_trust_defaults_on() -> None:
    """Cursor provider trusts the workspace when AIConfig uses the default."""
    from lintro.ai.enums import AITransport
    from lintro.ai.providers.cursor import CursorProvider

    config = AIConfig(
        provider="cursor",  # type: ignore[arg-type]  # Pydantic coerces str
        transport=AITransport.CLI,
    )
    with patch(
        "lintro.ai.providers.cursor._find_agent",
        return_value="/usr/local/bin/agent",
    ):
        provider = get_provider(config)
    assert_that(isinstance(provider, CursorProvider)).is_true()
    cursor = cast(CursorProvider, provider)
    assert_that(cursor._trust_workspace).is_true()


def test_get_provider_cursor_trust_opted_out() -> None:
    """get_provider threads an explicit cursor_trust_workspace=False opt-out."""
    from lintro.ai.enums import AITransport
    from lintro.ai.providers.cursor import CursorProvider

    config = AIConfig(
        provider="cursor",  # type: ignore[arg-type]  # Pydantic coerces str
        transport=AITransport.CLI,
        cursor_trust_workspace=False,
    )
    with patch(
        "lintro.ai.providers.cursor._find_agent",
        return_value="/usr/local/bin/agent",
    ):
        provider = get_provider(config)
    assert_that(isinstance(provider, CursorProvider)).is_true()
    cursor = cast(CursorProvider, provider)
    assert_that(cursor._trust_workspace).is_false()


def test_get_provider_anthropic_threads_cli_bare() -> None:
    """get_provider threads ai.cli_bare into the Anthropic provider."""
    from lintro.ai.enums import AITransport, CliBareMode
    from lintro.ai.providers.anthropic import AnthropicProvider

    config = AIConfig(
        provider="anthropic",  # type: ignore[arg-type]  # Pydantic coerces str
        transport=AITransport.CLI,
        cli_bare=CliBareMode.NEVER,
    )
    with patch.object(
        anthropic_mod,
        "_find_claude",
        return_value="/usr/local/bin/claude",
    ):
        provider = get_provider(config)
    assert_that(isinstance(provider, AnthropicProvider)).is_true()
    anthropic_provider = cast(AnthropicProvider, provider)
    assert_that(anthropic_provider._cli_bare).is_equal_to(CliBareMode.NEVER)
