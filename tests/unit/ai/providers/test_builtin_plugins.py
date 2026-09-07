"""Characterization and parity tests for the migrated provider plugins (#2307).

These pin the behaviour the migration had to preserve: ``get_provider``
resolves through the registry instead of a class map, every provider still
builds the same class with the same config fields for both transports, the
factory's error text is unchanged, and importing a provider package does not
drag in its vendor SDK.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.availability import provider_api_key_env, provider_cli_binary
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers import get_provider
from lintro.ai.providers.anthropic.provider import AnthropicProvider
from lintro.ai.providers.builtins import load_builtin_providers
from lintro.ai.providers.cli_contracts import cli_contract_for
from lintro.ai.providers.cursor.provider import CursorProvider
from lintro.ai.providers.protocol import ProviderPlugin
from lintro.ai.providers.registry import (
    all_providers,
    clear_registered,
    get_registered,
    is_registered,
    restore_registered,
)
from lintro.ai.registry import metadata_for

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Where each provider's CLI-binary lookup lives, so a CLI-transport
#: characterization test can pretend the binary is installed.
_BINARY_FINDERS: dict[AIProvider, str] = {
    AIProvider.ANTHROPIC: "lintro.ai.providers.anthropic.provider._find_claude",
    AIProvider.OPENAI: "lintro.ai.providers.openai.provider._find_codex",
    AIProvider.CURSOR: "lintro.ai.providers.cursor.provider._find_agent",
}

#: Where each SDK-backed provider records whether its vendor SDK imported. The
#: optional ``ai`` extra is not installed in every environment, so an
#: API-transport test says the SDK is present rather than requiring it.
_SDK_FLAGS: dict[AIProvider, str] = {
    AIProvider.ANTHROPIC: "lintro.ai.providers.anthropic.provider._has_anthropic",
    AIProvider.OPENAI: "lintro.ai.providers.openai.provider._has_openai",
}

#: Class each provider must still build, by qualified name. Compared as a
#: string so the assertion does not import three vendor SDKs.
_EXPECTED_CLASSES: dict[AIProvider, str] = {
    AIProvider.ANTHROPIC: "AnthropicProvider",
    AIProvider.OPENAI: "OpenAIProvider",
    AIProvider.CURSOR: "CursorProvider",
}


@pytest.fixture()
def _registered() -> Iterator[None]:
    """Guarantee the in-tree plugins are registered for the test.

    Yields:
        None: For the duration of the test.
    """
    load_builtin_providers()
    yield


@pytest.mark.usefixtures("_registered")
@pytest.mark.parametrize("provider", list(AIProvider))
def test_every_provider_has_a_registered_plugin(provider: AIProvider) -> None:
    """Discovery registers a plugin for each enum member.

    Args:
        provider: The provider under test.
    """
    plugin = get_registered(provider)

    assert_that(isinstance(plugin, ProviderPlugin)).is_true()
    assert_that(plugin.name).is_equal_to(provider)
    assert_that(plugin.metadata.provider).is_equal_to(provider)


@pytest.mark.usefixtures("_registered")
def test_registry_enumerates_providers_in_enum_order() -> None:
    """``all_providers`` is ordered by the enum, not by import order."""
    assert_that(list(all_providers())).is_equal_to(list(AIProvider))


@pytest.mark.usefixtures("_registered")
@pytest.mark.parametrize("provider", list(AIProvider))
def test_every_consumer_reads_the_same_metadata_record(
    provider: AIProvider,
) -> None:
    """The facade, availability and the contract lookup all answer alike.

    Args:
        provider: The provider under test.
    """
    metadata = get_registered(provider).metadata

    assert_that(metadata).is_same_as(metadata_for(provider))
    assert_that(metadata.default_api_key_env).is_equal_to(
        provider_api_key_env(provider),
    )
    assert_that(metadata.cli_binary).is_equal_to(provider_cli_binary(provider))
    assert_that(metadata.cli_binary).is_equal_to(
        cli_contract_for(provider).binary,
    )
    assert_that(metadata.cli_contract).is_same_as(cli_contract_for(provider))


@pytest.mark.usefixtures("_registered")
@pytest.mark.parametrize("provider", list(AIProvider))
def test_plugin_declares_the_transports_it_serves(provider: AIProvider) -> None:
    """Cursor is CLI-only; the SDK-backed providers serve both transports.

    Args:
        provider: The provider under test.
    """
    transports = get_registered(provider).transports

    if provider is AIProvider.CURSOR:
        assert_that(transports).is_equal_to(frozenset({AITransport.CLI}))
    else:
        assert_that(transports).is_equal_to(
            frozenset({AITransport.API, AITransport.CLI}),
        )


@pytest.mark.parametrize(
    "provider",
    [AIProvider.ANTHROPIC, AIProvider.OPENAI],
)
def test_get_provider_builds_the_same_class_over_api_transport(
    provider: AIProvider,
) -> None:
    """API transport still yields each vendor's own provider class.

    Args:
        provider: The provider under test.
    """
    config = AIConfig(provider=provider, transport=AITransport.API)

    with patch(_SDK_FLAGS[provider], True):
        built = get_provider(config)

    assert_that(type(built).__name__).is_equal_to(_EXPECTED_CLASSES[provider])
    assert_that(built.name).is_equal_to(provider.value)
    assert_that(built.model_name).is_equal_to(
        metadata_for(provider).default_model,
    )


@pytest.mark.parametrize("provider", list(AIProvider))
def test_get_provider_builds_the_same_class_over_cli_transport(
    provider: AIProvider,
) -> None:
    """CLI transport still yields each vendor's own provider class.

    Args:
        provider: The provider under test.
    """
    config = AIConfig(provider=provider, transport=AITransport.CLI)

    with patch(_BINARY_FINDERS[provider], return_value="/usr/local/bin/fake"):
        built = get_provider(config)

    assert_that(type(built).__name__).is_equal_to(_EXPECTED_CLASSES[provider])
    assert_that(built.name).is_equal_to(provider.value)


def test_get_provider_threads_the_model_and_api_key_env() -> None:
    """Shared config fields still reach the constructed provider."""
    config = AIConfig(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.API,
        model="claude-opus-4-20250514",
        api_key_env="CUSTOM_ANTHROPIC_KEY",
        max_tokens=1234,
    )

    with patch(_SDK_FLAGS[AIProvider.ANTHROPIC], True):
        built = get_provider(config)

    assert_that(built.model_name).is_equal_to("claude-opus-4-20250514")
    assert_that(built._api_key_env).is_equal_to("CUSTOM_ANTHROPIC_KEY")
    assert_that(built._max_tokens).is_equal_to(1234)


def test_get_provider_threads_the_anthropic_only_knob() -> None:
    """``cli_bare`` is read by the Anthropic plugin, not by the factory."""
    config = AIConfig(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        cli_bare=CliBareMode.NEVER,
    )

    with patch(
        _BINARY_FINDERS[AIProvider.ANTHROPIC],
        return_value="/usr/local/bin/claude",
    ):
        built = get_provider(config)

    assert_that(cast(AnthropicProvider, built)._cli_bare).is_equal_to(
        CliBareMode.NEVER,
    )


@pytest.mark.parametrize("trust", [True, False])
def test_get_provider_threads_the_cursor_only_knob(trust: bool) -> None:
    """``cursor_trust_workspace`` is read by the Cursor plugin.

    Args:
        trust: The configured workspace-trust value.
    """
    config = AIConfig(
        provider=AIProvider.CURSOR,
        transport=AITransport.CLI,
        cursor_trust_workspace=trust,
    )

    with patch(
        _BINARY_FINDERS[AIProvider.CURSOR],
        return_value="/usr/local/bin/agent",
    ):
        built = get_provider(config)

    assert_that(cast(CursorProvider, built)._trust_workspace).is_equal_to(trust)


def test_cursor_still_rejects_api_transport_with_its_own_message() -> None:
    """The unset-transport fallback stays ``api``, so Cursor's guard still fires."""
    from lintro.ai.exceptions import AINotAvailableError

    config = AIConfig.model_construct(
        provider=AIProvider.CURSOR,
        transport=None,
        model=None,
        api_key_env=None,
        max_tokens=4096,
    )

    with (
        patch(
            _BINARY_FINDERS[AIProvider.CURSOR],
            return_value="/usr/local/bin/agent",
        ),
        pytest.raises(AINotAvailableError) as excinfo,
    ):
        get_provider(config)

    assert_that(str(excinfo.value)).is_equal_to(
        "cursor provider only supports transport: cli",
    )


def test_unknown_provider_message_is_unchanged() -> None:
    """The pre-migration ``ValueError`` text survives the registry swap."""
    config = AIConfig.model_construct(provider="unknown")

    with pytest.raises(ValueError) as excinfo:
        get_provider(config)

    assert_that(str(excinfo.value)).is_equal_to(
        "Unknown AI provider: 'unknown'. "
        "Supported providers: anthropic, cursor, openai",
    )


def test_recognized_provider_without_a_plugin_reports_what_is_implemented() -> None:
    """A known name with no plugin keeps the "not implemented" ``ValueError``."""
    saved = all_providers()
    clear_registered()
    try:
        with (
            patch(
                "lintro.ai.providers.load_builtin_providers",
                lambda: None,
            ),
            pytest.raises(ValueError) as excinfo,
        ):
            get_provider(AIConfig(provider=AIProvider.ANTHROPIC))
    finally:
        restore_registered(saved)

    assert_that(str(excinfo.value)).is_equal_to(
        "AI provider 'anthropic' is recognized but not implemented. "
        "Implemented providers: ",
    )


def test_discovery_reregisters_after_the_registry_is_cleared() -> None:
    """A cleared registry is repopulated even though the modules are imported."""
    saved = all_providers()
    clear_registered()
    try:
        assert_that(is_registered(AIProvider.ANTHROPIC)).is_false()

        load_builtin_providers()

        assert_that(list(all_providers())).is_equal_to(list(AIProvider))
    finally:
        restore_registered(saved)


def test_discovery_does_not_import_any_vendor_sdk() -> None:
    """Registering all providers stays as cheap as the old lazy factory import."""
    import subprocess  # nosec B404 - fixed argv list, no shell
    import sys

    script = (
        "import sys\n"
        "from lintro.ai.providers.builtins import load_builtin_providers\n"
        "load_builtin_providers()\n"
        "print(int('anthropic' in sys.modules), int('openai' in sys.modules))\n"
    )
    completed = subprocess.run(  # nosec B603 - fixed argv list, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )

    assert_that(completed.stdout.strip()).is_equal_to("0 0")


def test_factory_no_longer_carries_a_provider_class_map() -> None:
    """The hardcoded ``provider_classes`` dict is gone from the AI package."""
    from pathlib import Path

    import lintro.ai as ai_package

    root = Path(str(ai_package.__file__)).parent
    offenders = [
        str(path)
        for path in root.rglob("*.py")
        if "provider_classes" in path.read_text(encoding="utf-8")
    ]

    assert_that(offenders).is_empty()
