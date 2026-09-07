"""Equality gate for the per-plugin provider metadata migration (#2308).

Every provider fact used to be declared in a table outside the provider's own
package: :data:`lintro.ai.registry.PROVIDERS` (defaults, pricing, API-key
variables), ``lintro.ai.availability._CLI_BINARIES`` (CLI binaries),
``lintro.ai.providers.cli_contracts.CLI_CONTRACTS`` (flag contracts) and the
``if provider == ...`` ladders in :mod:`lintro.ai.doctor_checks` (install hints
and auth probes).

This module asserts, field by field, that the metadata each plugin now declares
reproduces those tables exactly. It runs *before* they are deleted, so the
deletion commit is provably a move rather than a rewrite. Once the tables are
gone it is replaced by the guard in
``tests/unit/ai/providers/test_metadata_single_source.py``.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.builtins import load_builtin_providers
from lintro.ai.providers.cli_auth_probe import CliAuthProbe
from lintro.ai.providers.cli_contracts import CLI_CONTRACTS
from lintro.ai.providers.protocol import ProviderMetadata
from lintro.ai.providers.registry import get_registered
from lintro.ai.registry import PROVIDERS

#: The CLI binary table that lived in :mod:`lintro.ai.availability`.
_LEGACY_CLI_BINARIES: dict[AIProvider, str] = {
    AIProvider.ANTHROPIC: "claude",
    AIProvider.OPENAI: "codex",
    AIProvider.CURSOR: "agent",
}

#: The install hints that lived in ``doctor_checks._cli_install_hint``.
_LEGACY_INSTALL_HINTS: dict[AIProvider, str] = {
    AIProvider.CURSOR: "Install agent CLI: curl https://cursor.com/install -fsS | bash",
    AIProvider.ANTHROPIC: "Install Claude Code: https://code.claude.com/docs/en/setup",
    AIProvider.OPENAI: "Install Codex CLI: https://developers.openai.com/codex/cli",
}

#: The ``(configured_message, unverified_message, hint)`` triples that lived in
#: ``doctor_checks._check_cli_auth``.
_LEGACY_AUTH_MESSAGES: dict[AIProvider, tuple[str, str, str]] = {
    AIProvider.CURSOR: (
        "{key_env} is set",
        "Cursor CLI auth not verified",
        "Run `agent login` or set CURSOR_API_KEY",
    ),
    AIProvider.ANTHROPIC: (
        "{key_env} set (API billing overrides subscription)",
        "Claude CLI auth not verified",
        "Run `claude login` or set ANTHROPIC_API_KEY",
    ),
    AIProvider.OPENAI: (
        "Codex auth configured (CODEX_API_KEY or ~/.codex/auth.json)",
        "Codex CLI auth not verified",
        "Run `codex login` or set CODEX_API_KEY",
    ),
}

#: The transports each plugin advertised before they moved onto the metadata.
_LEGACY_TRANSPORTS: dict[AIProvider, frozenset[AITransport]] = {
    AIProvider.ANTHROPIC: frozenset({AITransport.API, AITransport.CLI}),
    AIProvider.OPENAI: frozenset({AITransport.API, AITransport.CLI}),
    AIProvider.CURSOR: frozenset({AITransport.CLI}),
}


def _metadata(provider: AIProvider) -> ProviderMetadata:
    """Return the registered plugin's metadata for *provider*.

    Args:
        provider: The provider to look up.

    Returns:
        The plugin's metadata record.
    """
    load_builtin_providers()
    return get_registered(provider).metadata


def _auth_probe(provider: AIProvider) -> CliAuthProbe:
    """Return the declared CLI auth probe for *provider*.

    Args:
        provider: The provider to look up.

    Returns:
        The provider's auth probe.

    Raises:
        AssertionError: If the provider declares no probe. Every provider
            lintro ships has a CLI transport, so a missing probe is the
            migration having dropped one.
    """
    probe = _metadata(provider).cli_auth_probe
    if probe is None:
        raise AssertionError(f"{provider.value} declares no CLI auth probe")
    return probe


@pytest.mark.parametrize("provider", list(AIProvider))
def test_metadata_reproduces_registry_defaults(provider: AIProvider) -> None:
    """Plugin metadata restates the old PROVIDERS defaults exactly.

    Args:
        provider: The provider under test.
    """
    info = PROVIDERS.get(provider)
    metadata = _metadata(provider)
    assert_that(metadata.default_model).is_equal_to(info.default_model)
    assert_that(metadata.default_api_key_env).is_equal_to(info.default_api_key_env)


@pytest.mark.parametrize("provider", list(AIProvider))
def test_metadata_reproduces_registry_pricing(provider: AIProvider) -> None:
    """Plugin metadata restates the old PROVIDERS pricing exactly.

    Args:
        provider: The provider under test.
    """
    info = PROVIDERS.get(provider)
    metadata = _metadata(provider)
    assert_that(dict(metadata.pricing)).is_equal_to(dict(info.models))
    assert_that(list(metadata.pricing_keys)).is_equal_to(list(info.models))


def test_merged_pricing_reproduces_the_flat_registry_table() -> None:
    """The union of every plugin's pricing equals the old flat pricing table."""
    merged: dict[str, object] = {}
    for provider in AIProvider:
        merged.update(_metadata(provider).pricing)
    assert_that(merged).is_equal_to(PROVIDERS.model_pricing)


@pytest.mark.parametrize("provider", list(AIProvider))
def test_metadata_reproduces_cli_binary_table(provider: AIProvider) -> None:
    """Plugin metadata restates the old availability CLI-binary table.

    Args:
        provider: The provider under test.
    """
    assert_that(_metadata(provider).cli_binary).is_equal_to(
        _LEGACY_CLI_BINARIES[provider],
    )


@pytest.mark.parametrize("provider", list(AIProvider))
def test_metadata_reproduces_cli_contract(provider: AIProvider) -> None:
    """Plugin metadata carries the identical CLI contract object.

    Args:
        provider: The provider under test.
    """
    assert_that(_metadata(provider).cli_contract).is_equal_to(CLI_CONTRACTS[provider])


@pytest.mark.parametrize("provider", list(AIProvider))
def test_metadata_reproduces_doctor_install_hint(provider: AIProvider) -> None:
    """Plugin metadata restates doctor's per-provider install hint.

    Args:
        provider: The provider under test.
    """
    assert_that(_metadata(provider).cli_install_hint).is_equal_to(
        _LEGACY_INSTALL_HINTS[provider],
    )


@pytest.mark.parametrize("provider", list(AIProvider))
def test_metadata_reproduces_doctor_auth_messages(provider: AIProvider) -> None:
    """Plugin metadata restates doctor's per-provider auth messages.

    Args:
        provider: The provider under test.
    """
    probe = _auth_probe(provider)
    expected = _LEGACY_AUTH_MESSAGES[provider]
    assert_that(
        (probe.configured_message, probe.unverified_message, probe.hint),
    ).is_equal_to(expected)


@pytest.mark.parametrize("provider", list(AIProvider))
def test_metadata_reproduces_plugin_transports(provider: AIProvider) -> None:
    """Plugin metadata restates the transports the plugin advertised.

    Args:
        provider: The provider under test.
    """
    metadata = _metadata(provider)
    assert_that(metadata.supported_transports).is_equal_to(
        _LEGACY_TRANSPORTS[provider],
    )
    assert_that(get_registered(provider).transports).is_equal_to(
        _LEGACY_TRANSPORTS[provider],
    )
