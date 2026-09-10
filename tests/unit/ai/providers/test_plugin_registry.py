"""Tests for the AI provider plugin contract and registry (#2306).

These exercise the registry against a fake plugin, so every test that touches
it clears the real in-tree registrations first and restores them afterwards.
The migrated providers are covered by ``test_builtin_plugins.py`` instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AIError,
    AIProviderAlreadyRegisteredError,
    AIProviderNotRegisteredError,
    AIProviderRegistrationError,
)
from lintro.ai.model_pricing import ModelPricing
from lintro.ai.provider_config import ProviderConfig
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.protocol import (
    PROVIDER_PLUGIN_API_VERSION,
    ProviderMetadata,
    ProviderPlugin,
)
from lintro.ai.providers.registry import (
    all_providers,
    clear_registered,
    get_registered,
    is_registered,
    register_provider,
    restore_registered,
)

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider


def _metadata_for(provider: AIProvider) -> ProviderMetadata:
    """Build fake metadata describing *provider*.

    Args:
        provider: Provider the metadata describes.

    Returns:
        A metadata record whose ``provider`` matches *provider*.
    """
    return ProviderMetadata(
        provider=provider,
        display_name="Fake",
        default_model="fake-model",
        default_api_key_env="FAKE_API_KEY",
        supported_transports=frozenset({AITransport.API}),
        default_transport=AITransport.API,
        sdk_package="fake-sdk",
        cli_binary="fake-cli",
        cli_contract_id="fake",
        pricing={"fake-model": ModelPricing(1.0, 2.0)},
    )


@dataclass
class _FakeProvider:
    """Stand-in for a built provider instance.

    Attributes:
        model: Model identifier the plugin was asked to build.
        closed: Whether ``aclose`` has run.
    """

    model: str | None = None
    closed: bool = False

    async def aclose(self) -> None:
        """Record that the caller closed this provider."""
        self.closed = True


@dataclass(frozen=True, kw_only=True)
class _FakePlugin:
    """Minimal plugin satisfying the ``ProviderPlugin`` contract.

    Attributes:
        name: Provider identity used as the registry key.
        transports: Transports this fake claims to serve.
        metadata: Static description of the fake provider.
        config_model: Model for this fake's ``ai.providers.<name>`` block.
        built: Models this plugin was asked to build, in call order.
    """

    name: AIProvider = AIProvider.ANTHROPIC
    transports: frozenset[AITransport] = frozenset({AITransport.API})
    metadata: ProviderMetadata = _metadata_for(AIProvider.ANTHROPIC)
    config_model: type[ProviderConfig] = ProviderConfig
    built: list[str | None] = field(default_factory=list)

    def build(self, config: AIConfig) -> BaseAIProvider:
        """Build the fake provider described by *config*.

        Args:
            config: Effective AI configuration for this run.

        Returns:
            A fake provider carrying the requested model.
        """
        self.built.append(config.model)
        return _FakeProvider(model=config.model)  # type: ignore[return-value]


@pytest.fixture()
def _clean_registry() -> Iterator[None]:
    """Run the test against an empty registry, then restore what was there.

    Yields:
        None: For the duration of the test.
    """
    saved = all_providers()
    clear_registered()
    try:
        yield
    finally:
        restore_registered(saved)


@pytest.mark.usefixtures("_clean_registry")
def test_register_provider_makes_the_plugin_resolvable() -> None:
    """A registered plugin is returned by name lookup and by enumeration."""
    plugin = _FakePlugin()

    returned = register_provider(plugin)

    assert_that(returned).is_same_as(plugin)
    assert_that(get_registered(AIProvider.ANTHROPIC)).is_same_as(plugin)
    assert_that(all_providers()).is_equal_to({AIProvider.ANTHROPIC: plugin})


@pytest.mark.usefixtures("_clean_registry")
def test_get_registered_accepts_the_string_a_user_typed() -> None:
    """Lookup normalises case so ``--provider ANTHROPIC`` resolves."""
    plugin = _FakePlugin()
    register_provider(plugin)

    assert_that(get_registered("ANTHROPIC")).is_same_as(plugin)
    assert_that(is_registered("anthropic")).is_true()


@pytest.mark.usefixtures("_clean_registry")
def test_registering_a_duplicate_name_raises() -> None:
    """A second plugin for the same provider is rejected, not silently swapped."""
    first = _FakePlugin()
    register_provider(first)

    with pytest.raises(AIProviderAlreadyRegisteredError) as excinfo:
        register_provider(_FakePlugin())

    assert_that(str(excinfo.value)).contains("anthropic", "already registered")
    assert_that(get_registered(AIProvider.ANTHROPIC)).is_same_as(first)


@pytest.mark.usefixtures("_clean_registry")
def test_unknown_provider_name_raises_with_accepted_values() -> None:
    """A name outside the enum names what lintro accepts."""
    with pytest.raises(AIProviderNotRegisteredError) as excinfo:
        get_registered("not-a-provider")

    message = str(excinfo.value)
    assert_that(message).contains("not-a-provider", "anthropic", "cursor", "openai")
    assert_that(is_registered("not-a-provider")).is_false()


@pytest.mark.usefixtures("_clean_registry")
def test_known_provider_without_a_plugin_raises() -> None:
    """A real provider name with nothing registered is a distinct message."""
    register_provider(_FakePlugin())

    with pytest.raises(AIProviderNotRegisteredError) as excinfo:
        get_registered(AIProvider.CURSOR)

    message = str(excinfo.value)
    assert_that(message).contains("cursor", "no registered plugin")
    assert_that(message).contains("Registered providers: anthropic")


@pytest.mark.usefixtures("_clean_registry")
def test_all_providers_is_ordered_by_enum_not_registration() -> None:
    """Enumeration order is stable regardless of which plugin registered first."""
    register_provider(
        _FakePlugin(
            name=AIProvider.CURSOR,
            metadata=_metadata_for(AIProvider.CURSOR),
        ),
    )
    register_provider(_FakePlugin(name=AIProvider.ANTHROPIC))

    assert_that(list(all_providers())).is_equal_to(
        [AIProvider.ANTHROPIC, AIProvider.CURSOR],
    )


@pytest.mark.usefixtures("_clean_registry")
def test_all_providers_returns_a_copy() -> None:
    """Mutating the returned mapping does not touch the registry."""
    plugin = _FakePlugin()
    register_provider(plugin)

    snapshot = all_providers()
    snapshot.clear()

    assert_that(get_registered(AIProvider.ANTHROPIC)).is_same_as(plugin)


@pytest.mark.usefixtures("_clean_registry")
def test_build_receives_the_config_and_returns_a_provider(
    ai_config: AIConfig,
) -> None:
    """The plugin, not the caller, assembles the per-vendor constructor call."""
    plugin = _FakePlugin()
    register_provider(plugin)

    built = get_registered("anthropic").build(ai_config)

    assert_that(plugin.built).is_equal_to([ai_config.model])
    assert_that(built).is_instance_of(_FakeProvider)


def test_registry_errors_share_the_ai_error_hierarchy() -> None:
    """Existing ``except AIError`` boundaries keep catching registry failures."""
    assert_that(AIProviderRegistrationError.__mro__).contains(AIError)
    assert_that(AIProviderAlreadyRegisteredError.__mro__).contains(
        AIProviderRegistrationError,
    )
    assert_that(AIProviderNotRegisteredError.__mro__).contains(
        AIProviderRegistrationError,
    )


def test_fake_plugin_satisfies_the_runtime_protocol() -> None:
    """The declared protocol is checkable, so parity tests can assert on it."""
    assert_that(isinstance(_FakePlugin(), ProviderPlugin)).is_true()
    assert_that(isinstance(object(), ProviderPlugin)).is_false()


def test_metadata_exposes_pricing_keys_without_importing_a_sdk() -> None:
    """Metadata answers pricing questions from the record alone."""
    metadata = _FakePlugin().metadata

    assert_that(metadata.pricing_keys).is_equal_to(("fake-model",))
    assert_that(metadata.provider).is_equal_to(AIProvider.ANTHROPIC)
    # v2 since #2309 added the required ``config_model`` member.
    assert_that(PROVIDER_PLUGIN_API_VERSION).is_equal_to(2)


@pytest.mark.parametrize(
    ("supported", "default"),
    [
        (frozenset({AITransport.API}), AITransport.CLI),
        (frozenset({AITransport.CLI}), AITransport.API),
        (frozenset(), AITransport.API),
    ],
)
def test_a_default_transport_the_provider_cannot_serve_is_rejected(
    supported: frozenset[AITransport],
    default: AITransport,
) -> None:
    """A record cannot advertise a default outside its supported transports.

    Doctor and the generated docs table steer users towards
    ``default_transport``, so an unserveable one is rejected where it is
    declared rather than surfacing as an unusable hint.

    Args:
        supported: Transports the fake provider claims to serve.
        default: The default it declares, which is not among them.
    """
    with pytest.raises(ValueError) as excinfo:
        ProviderMetadata(
            provider=AIProvider.ANTHROPIC,
            display_name="Fake",
            default_model="fake-model",
            default_api_key_env="FAKE_API_KEY",
            supported_transports=supported,
            default_transport=default,
        )

    assert_that(str(excinfo.value)).contains("anthropic", default.value)


def test_provider_metadata_is_frozen() -> None:
    """Metadata cannot be edited in place by a consumer."""
    metadata = _FakePlugin().metadata

    with pytest.raises(AttributeError):
        metadata.default_model = "other"  # type: ignore[misc]


@pytest.mark.usefixtures("_clean_registry")
def test_metadata_naming_another_provider_is_rejected() -> None:
    """A plugin cannot describe one vendor and register as another."""
    plugin = _FakePlugin(name=AIProvider.OPENAI)

    with pytest.raises(AIProviderRegistrationError) as excinfo:
        register_provider(plugin)

    assert_that(str(excinfo.value)).contains("openai", "anthropic", "must agree")
    assert_that(is_registered(AIProvider.OPENAI)).is_false()


def test_metadata_pricing_cannot_be_mutated_in_place() -> None:
    """Pricing is stored behind a read-only view, not the caller's dict."""
    source = {"fake-model": ModelPricing(1.0, 2.0)}
    metadata = ProviderMetadata(
        provider=AIProvider.ANTHROPIC,
        display_name="Fake",
        default_model="fake-model",
        default_api_key_env="FAKE_API_KEY",
        supported_transports=frozenset({AITransport.API}),
        default_transport=AITransport.API,
        pricing=source,
    )

    with pytest.raises(TypeError):
        metadata.pricing["other"] = ModelPricing(3.0, 4.0)  # type: ignore[index]

    source["other"] = ModelPricing(3.0, 4.0)

    assert_that(metadata.pricing_keys).is_equal_to(("fake-model",))
