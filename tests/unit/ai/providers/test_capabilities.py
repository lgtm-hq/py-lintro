"""Tests for provider capability declarations (#1241)."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.providers import anthropic as anthropic_module
from lintro.ai.providers import openai as openai_module
from lintro.ai.providers.anthropic import AnthropicProvider
from lintro.ai.providers.base import BaseAIProvider, ProviderCapabilities
from lintro.ai.providers.cursor import CursorProvider
from lintro.ai.providers.openai import OpenAIProvider


@pytest.fixture()
def _cli_binaries_on_path() -> Iterator[None]:
    """Pretend every agent CLI and provider SDK is installed.

    Yields:
        None: For the duration of the patched lookups.
    """
    with (
        patch(
            "lintro.ai.providers.anthropic._find_claude",
            return_value="/usr/local/bin/claude",
        ),
        patch(
            "lintro.ai.providers.cursor._find_agent",
            return_value="/usr/local/bin/agent",
        ),
        patch(
            "lintro.ai.providers.openai._find_codex",
            return_value="/usr/local/bin/codex",
        ),
        patch.object(anthropic_module, "_has_anthropic", True),
        patch.object(openai_module, "_has_openai", True),
    ):
        yield


def test_default_capabilities_are_conservative() -> None:
    """Declare nothing supported until a provider says otherwise."""
    capabilities = ProviderCapabilities()

    assert_that(capabilities.supports_sessions).is_false()
    assert_that(capabilities.supports_structured_output).is_false()
    assert_that(capabilities.supports_streaming).is_false()


def test_capabilities_are_frozen() -> None:
    """Reject mutation of a capability declaration."""
    capabilities = ProviderCapabilities(supports_sessions=True)

    with pytest.raises(AttributeError):
        capabilities.supports_sessions = False  # type: ignore[misc]


def test_anthropic_cli_declares_sessions_and_structured_output(
    _cli_binaries_on_path: None,
) -> None:
    """Claude CLI resumes sessions and takes a native JSON schema."""
    provider = AnthropicProvider(transport=AITransport.CLI)

    assert_that(provider.capabilities.supports_sessions).is_true()
    assert_that(provider.capabilities.supports_structured_output).is_true()
    assert_that(provider.capabilities.supports_streaming).is_false()


def test_anthropic_api_declares_streaming(_cli_binaries_on_path: None) -> None:
    """The Anthropic API streams but exposes no resumable session."""
    provider = AnthropicProvider(transport=AITransport.API)

    assert_that(provider.capabilities.supports_sessions).is_false()
    assert_that(provider.capabilities.supports_streaming).is_true()


def test_cursor_declares_sessions_without_structured_output(
    _cli_binaries_on_path: None,
) -> None:
    """The Cursor agent resumes sessions but has no native schema support."""
    provider = CursorProvider()

    assert_that(provider.capabilities.supports_sessions).is_true()
    assert_that(provider.capabilities.supports_structured_output).is_false()


def test_codex_declares_structured_output_without_sessions(
    _cli_binaries_on_path: None,
) -> None:
    """Codex takes an output schema but exposes no session lintro can resume."""
    provider = OpenAIProvider(transport=AITransport.CLI)

    assert_that(provider.capabilities.supports_sessions).is_false()
    assert_that(provider.capabilities.supports_structured_output).is_true()


def test_openai_api_declares_streaming(_cli_binaries_on_path: None) -> None:
    """The OpenAI API streams but exposes no resumable session."""
    provider = OpenAIProvider(transport=AITransport.API)

    assert_that(provider.capabilities.supports_sessions).is_false()
    assert_that(provider.capabilities.supports_streaming).is_true()


def test_every_provider_exposes_a_declaration(_cli_binaries_on_path: None) -> None:
    """Every provider answers with a ProviderCapabilities instance."""
    providers: list[BaseAIProvider] = [
        AnthropicProvider(transport=AITransport.CLI),
        AnthropicProvider(transport=AITransport.API),
        CursorProvider(),
        OpenAIProvider(transport=AITransport.CLI),
        OpenAIProvider(transport=AITransport.API),
    ]

    for provider in providers:
        assert_that(provider.capabilities).is_instance_of(ProviderCapabilities)
