"""Tests for AI provider factory."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.providers import anthropic as anthropic_mod
from lintro.ai.providers import get_provider
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


def test_get_provider_cursor_trust_defaults_off():
    """Cursor provider does not trust the workspace unless opted in."""
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
    assert_that(cursor._trust_workspace).is_false()


def test_get_provider_cursor_trust_threaded_when_enabled():
    """get_provider threads cursor_trust_workspace into the provider."""
    from lintro.ai.enums import AITransport
    from lintro.ai.providers.cursor import CursorProvider

    config = AIConfig(
        provider="cursor",  # type: ignore[arg-type]  # Pydantic coerces str
        transport=AITransport.CLI,
        cursor_trust_workspace=True,
    )
    with patch(
        "lintro.ai.providers.cursor._find_agent",
        return_value="/usr/local/bin/agent",
    ):
        provider = get_provider(config)
    assert_that(isinstance(provider, CursorProvider)).is_true()
    cursor = cast(CursorProvider, provider)
    assert_that(cursor._trust_workspace).is_true()
