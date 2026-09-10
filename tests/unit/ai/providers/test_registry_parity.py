"""Registry parity for every AI provider plugin (#2310).

Acceptance criterion 4 of #1999: the plugin contract is self-enforcing. A
vendor that omits a metadata field, forgets the lifecycle method, declares a
transport it cannot build, or names a CLI contract that does not exist must
fail here rather than at a user's first review.

The suite is parametrised over
:func:`~lintro.ai.providers.registry.all_providers`, so a provider added later
is covered the moment its package registers — nothing lists the three vendors
by name. Each rule is a module-level ``check_*`` function rather than an
inline assertion block, and the negative tests at the bottom run those very
functions against :class:`FakeIncompleteProvider`. A check that stopped
asserting would therefore stop failing on the fake too, which is the failure
mode a hand-written "this should fail" test cannot catch.

``config_model`` is validated against the example published in
``docs/ai-features.md`` rather than a copy of it: the documented
``ai.providers.<name>`` blocks are parsed out of the document's YAML fences,
so a renamed field must update the docs or fail the suite. Every provider is
documented today; a future one that is not falls back to asserting its block
accepts ``{}`` ("this provider, all defaults"), which is the weakest claim the
contract still makes.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import yaml
from assertpy import assert_that
from pydantic import ValidationError

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AINotAvailableError
from lintro.ai.model_pricing import ModelPricing
from lintro.ai.provider_config import ProviderConfig
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.base import BaseAIProvider
from lintro.ai.providers.builtins import load_builtin_providers
from lintro.ai.providers.cli_auth_probe import CliAuthProbe
from lintro.ai.providers.cli_contracts import cli_contracts
from lintro.ai.providers.protocol import ProviderMetadata
from lintro.ai.providers.registry import all_providers, get_registered

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from lintro.ai.providers.protocol import ProviderPlugin

#: The published document whose ``ai.providers.<name>`` examples are the
#: contract each plugin's ``config_model`` is validated against.
_DOC = Path(__file__).resolve().parents[4] / "docs" / "ai-features.md"

#: Fenced YAML blocks in that document.
_YAML_FENCE = re.compile(r"^```yaml\n(?P<body>.*?)^```", re.MULTILINE | re.DOTALL)

#: Where each provider's CLI-binary lookup lives, so a CLI-transport build can
#: proceed on a machine where the agent binary is not installed.
_BINARY_FINDERS: Mapping[AIProvider, str] = MappingProxyType(
    {
        AIProvider.ANTHROPIC: "lintro.ai.providers.anthropic.provider._find_claude",
        AIProvider.OPENAI: "lintro.ai.providers.openai.provider._find_codex",
        AIProvider.CURSOR: "lintro.ai.providers.cursor.provider._find_agent",
    },
)

#: Where each SDK-backed provider records whether its vendor SDK imported. The
#: optional ``ai`` extra is not installed in every environment, so an
#: API-transport build says the SDK is present rather than requiring it.
_SDK_FLAGS: Mapping[AIProvider, str] = MappingProxyType(
    {
        AIProvider.ANTHROPIC: "lintro.ai.providers.anthropic.provider._has_anthropic",
        AIProvider.OPENAI: "lintro.ai.providers.openai.provider._has_openai",
    },
)


def registered_providers() -> list[AIProvider]:
    """Return every provider with a registered plugin, in enum order.

    Discovery runs first so collection does not depend on which module
    happened to import a provider package.

    Returns:
        The registry keys, in :class:`~lintro.ai.provider_enum.AIProvider`
        declaration order.
    """
    load_builtin_providers()
    return list(all_providers())


def documented_provider_examples() -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    """Return the ``ai.providers.<name>`` examples published in the docs.

    Every YAML fence in ``docs/ai-features.md`` is parsed and any
    ``ai.providers`` mapping inside it contributes one example per provider,
    so both the annotated full-config block and the short "provider-specific
    settings" snippet are covered. A fence that is not valid YAML, or that
    carries no ``ai.providers`` mapping, is skipped rather than failing the
    parse: the document is prose with examples in it, not a config file.

    Returns:
        Provider name mapped to every documented block for it, in document
        order.
    """
    collected: dict[str, list[Mapping[str, Any]]] = {}
    text = _DOC.read_text(encoding="utf-8")
    for fence in _YAML_FENCE.finditer(text):
        try:
            data = yaml.safe_load(fence.group("body"))
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        section = data.get("ai")
        if not isinstance(section, dict):
            continue
        providers = section.get("providers")
        if not isinstance(providers, dict):
            continue
        for name, block in providers.items():
            resolved = {} if block is None else block
            if not isinstance(resolved, dict):
                continue
            collected.setdefault(str(name), []).append(resolved)
    return MappingProxyType({name: tuple(blocks) for name, blocks in collected.items()})


@contextmanager
def _transport_available(
    provider: AIProvider,
    transport: AITransport,
) -> Iterator[None]:
    """Pretend the backend *transport* needs is installed.

    A parity check is about the plugin contract, not about what this machine
    has on ``PATH`` or in its site-packages, so the vendor SDK flag and the
    CLI binary lookup are stubbed for the duration of the build. A provider
    with no known stub site (a test double) is yielded unchanged.

    Args:
        provider: The provider being built.
        transport: The transport being built for.

    Yields:
        None: While the backend looks available.
    """
    if transport is AITransport.API:
        sdk_flag = _SDK_FLAGS.get(provider)
        if sdk_flag is None:
            yield
            return
        with patch(sdk_flag, True):
            yield
        return
    finder = _BINARY_FINDERS.get(provider)
    if finder is None:
        yield
        return
    with patch(finder, return_value="/usr/local/bin/fake"):
        yield


def _config_for(provider: AIProvider, transport: AITransport) -> AIConfig:
    """Build the effective config a plugin is asked to construct from.

    Args:
        provider: The provider to select.
        transport: The transport to request.

    Returns:
        An :class:`~lintro.ai.config.AIConfig` carrying only the two fields
        the parity build depends on; every other field keeps its default.
    """
    return AIConfig(provider=provider, transport=transport)


def check_metadata_fields_are_populated(plugin: ProviderPlugin) -> None:
    """Assert every :class:`ProviderMetadata` field a plugin must fill is set.

    The always-required fields are the identity ones. The rest are required
    *conditionally on the transports the provider declares*: a CLI-only vendor
    has no SDK distribution to name, and an API-only one has no binary, so
    demanding both would force a lie into the record.

    Fails when a required field is unset, blank, or describes a different
    provider than the plugin builds.

    Args:
        plugin: The plugin under test.
    """
    metadata = plugin.metadata
    label = plugin.name.value
    assert_that(metadata.provider).described_as(
        f"{label} metadata.provider",
    ).is_equal_to(plugin.name)
    for name in ("display_name", "default_model", "default_api_key_env"):
        value = getattr(metadata, name)
        assert_that(value).described_as(f"{label} metadata.{name}").is_instance_of(str)
        assert_that(value.strip()).described_as(
            f"{label} metadata.{name} is blank",
        ).is_not_empty()
    assert_that(
        sorted(item.value for item in metadata.supported_transports),
    ).described_as(
        f"{label} metadata.supported_transports is empty",
    ).is_not_empty()
    assert_that(metadata.supports(metadata.default_transport)).described_as(
        f"{label} metadata.default_transport is not a supported transport",
    ).is_true()
    conditional: tuple[tuple[AITransport, tuple[str, ...]], ...] = (
        (AITransport.API, ("sdk_package",)),
        (
            AITransport.CLI,
            ("cli_binary", "cli_contract_id", "cli_install_hint", "cli_contract"),
        ),
    )
    for transport, names in conditional:
        if not metadata.supports(transport):
            continue
        for name in names:
            value = getattr(metadata, name)
            assert_that(value).described_as(
                f"{label} serves transport '{transport.value}' but "
                f"metadata.{name} is unset",
            ).is_not_none()
            if isinstance(value, str):
                assert_that(value.strip()).described_as(
                    f"{label} metadata.{name} is blank",
                ).is_not_empty()


def check_pricing_declares_a_priced_model(plugin: ProviderPlugin) -> None:
    """Assert the plugin publishes pricing for at least one model.

    Rates of zero are legitimate — a subscription CLI bills no per-token price
    — so the assertion is that the models are *enumerated*, not that they cost
    money. An empty table would silently drop the provider out of cost
    estimation.

    Fails when the pricing table is empty or malformed.

    Args:
        plugin: The plugin under test.
    """
    label = plugin.name.value
    pricing = dict(plugin.metadata.pricing)
    assert_that(pricing).described_as(
        f"{label} metadata.pricing declares no model",
    ).is_not_empty()
    for model, price in pricing.items():
        assert_that(model.strip()).described_as(
            f"{label} metadata.pricing has a blank model identifier",
        ).is_not_empty()
        assert_that(price).described_as(
            f"{label} metadata.pricing[{model!r}]",
        ).is_instance_of(ModelPricing)
        assert_that(
            min(price.input_per_million, price.output_per_million),
        ).described_as(
            f"{label} metadata.pricing[{model!r}] is negative",
        ).is_greater_than_or_equal_to(
            0.0,
        )


def check_credentials_are_declared(plugin: ProviderPlugin) -> None:
    """Assert doctor can say how this provider authenticates.

    Either the provider names an API-key environment variable, or it declares
    an explicit :class:`~lintro.ai.providers.cli_auth_probe.CliAuthProbe`.
    With neither, doctor has nothing to report and a misconfigured credential
    surfaces only as a vendor error mid-review.

    Fails when the plugin declares neither credential source.

    Args:
        plugin: The plugin under test.
    """
    metadata = plugin.metadata
    key_env = metadata.default_api_key_env
    declared = bool(isinstance(key_env, str) and key_env.strip()) or (
        metadata.cli_auth_probe is not None
    )
    assert_that(declared).described_as(
        f"{plugin.name.value} declares neither default_api_key_env nor "
        "cli_auth_probe, so doctor cannot report how it authenticates",
    ).is_true()


def check_declared_transports_build(plugin: ProviderPlugin) -> None:
    """Assert ``build`` honours exactly the transports the plugin declares.

    A declared transport must produce a
    :class:`~lintro.ai.providers.base.BaseAIProvider`; an undeclared one must
    be rejected at construction with
    :class:`~lintro.ai.exceptions.AINotAvailableError`, which is the error the
    contract documents, rather than failing later inside the vendor call.

    Args:
        plugin: The plugin under test.

    Raises:
        AssertionError: If a declared transport cannot be built, if it builds
            something that is not a provider, or if an undeclared transport is
            accepted or rejected with the wrong error.
    """
    label = plugin.name.value
    for transport in AITransport:
        declared = transport in plugin.transports
        config = _config_for(plugin.name, transport)
        with _transport_available(plugin.name, transport):
            if declared:
                try:
                    built = plugin.build(config)
                except Exception as exc:
                    raise AssertionError(
                        f"{label} declares transport '{transport.value}' but "
                        f"build() raised {type(exc).__name__}: {exc}",
                    ) from exc
                assert_that(built).described_as(
                    f"{label} build() on transport '{transport.value}'",
                ).is_instance_of(BaseAIProvider)
                continue
            try:
                plugin.build(config)
            except AINotAvailableError:
                continue
            except Exception as exc:
                raise AssertionError(
                    f"{label} rejects undeclared transport '{transport.value}' "
                    f"with {type(exc).__name__}, not AINotAvailableError",
                ) from exc
            raise AssertionError(
                f"{label} does not declare transport '{transport.value}' but "
                "build() accepted it instead of raising AINotAvailableError",
            )


async def check_aclose_is_idempotent(plugin: ProviderPlugin) -> None:
    """Assert the built provider closes cleanly, twice.

    Lifecycle is inherited from
    :class:`~lintro.ai.providers.base.BaseAIProvider` rather than reimplemented
    per plugin, so this is really a check that the plugin returns something
    that inherited it — and that a second close, which the review pipeline can
    reach on an error path, is a no-op rather than a crash.

    Args:
        plugin: The plugin under test.

    Raises:
        AssertionError: If the built provider exposes no ``aclose``, or if
            closing it twice raises.
    """
    label = plugin.name.value
    transport = plugin.metadata.default_transport
    with _transport_available(plugin.name, transport):
        built = plugin.build(_config_for(plugin.name, transport))
    aclose = getattr(built, "aclose", None)
    if not callable(aclose):
        raise AssertionError(
            f"{label} build() returned {type(built).__name__}, which has no "
            "aclose(); the provider lifecycle contract is unmet",
        )
    try:
        await aclose()
        await aclose()
    except Exception as exc:
        raise AssertionError(
            f"{label} aclose() is not idempotent: the second call raised "
            f"{type(exc).__name__}: {exc}",
        ) from exc


def check_config_model_validates_documented_examples(
    plugin: ProviderPlugin,
    examples: Sequence[Mapping[str, Any]],
) -> None:
    """Assert the plugin's block model accepts what the docs publish for it.

    Args:
        plugin: The plugin under test.
        examples: Documented ``ai.providers.<name>`` blocks. Empty when the
            document carries none, in which case the model must still accept
            an empty block — "this provider, all defaults".

    Raises:
        AssertionError: If the model rejects a documented block.
    """
    label = plugin.name.value
    blocks = list(examples) or [{}]
    for block in blocks:
        try:
            plugin.config_model.model_validate(dict(block))
        except ValidationError as exc:
            raise AssertionError(
                f"{label} config_model rejects its documented example "
                f"{dict(block)!r}: {exc}",
            ) from exc


def check_display_names_are_unique(
    plugins: Mapping[AIProvider, ProviderPlugin],
) -> None:
    """Assert no two providers present themselves under the same name.

    Display names reach users in doctor output, the cost summary and the
    generated provider table, where a collision makes two vendors
    indistinguishable.

    Fails when two plugins share a display name.

    Args:
        plugins: Every registered plugin, keyed by provider.
    """
    seen: dict[str, str] = {}
    for provider, plugin in plugins.items():
        name = plugin.metadata.display_name
        owner = seen.get(name)
        assert_that(owner).described_as(
            f"display name {name!r} is claimed by both {owner} and "
            f"{provider.value}",
        ).is_none()
        seen[name] = provider.value


def check_cli_contract_id_resolves(plugin: ProviderPlugin) -> None:
    """Assert ``cli_contract_id`` names a contract the registry can find.

    The id is a plain string so a caller that only needs the key does not
    import the contract definitions, which is exactly what lets it drift from
    the contract it points at.

    Fails when a CLI provider declares no id, when the id resolves to
    nothing, or when it resolves to a different contract than the one the
    metadata carries.

    Args:
        plugin: The plugin under test.
    """
    metadata = plugin.metadata
    label = plugin.name.value
    contract_id = metadata.cli_contract_id
    if contract_id is None:
        assert_that(metadata.supports(AITransport.CLI)).described_as(
            f"{label} serves CLI transport but declares no cli_contract_id",
        ).is_false()
        return
    by_id = {provider.value: contract for provider, contract in cli_contracts().items()}
    assert_that(by_id).described_as(
        f"{label} cli_contract_id {contract_id!r} resolves to no declared "
        "CLI contract",
    ).contains_key(contract_id)
    assert_that(by_id[contract_id]).described_as(
        f"{label} cli_contract_id {contract_id!r} resolves to a different "
        "contract than metadata.cli_contract",
    ).is_same_as(metadata.cli_contract)


@pytest.mark.parametrize("provider", registered_providers())
def test_metadata_fields_are_populated(provider: AIProvider) -> None:
    """Every registered plugin fills the metadata its consumers read.

    Args:
        provider: The provider under test.
    """
    check_metadata_fields_are_populated(get_registered(provider))


@pytest.mark.parametrize("provider", registered_providers())
def test_pricing_declares_a_priced_model(provider: AIProvider) -> None:
    """Every registered plugin enumerates at least one priced model.

    Args:
        provider: The provider under test.
    """
    check_pricing_declares_a_priced_model(get_registered(provider))


@pytest.mark.parametrize("provider", registered_providers())
def test_credentials_are_declared(provider: AIProvider) -> None:
    """Every registered plugin says how it authenticates.

    Args:
        provider: The provider under test.
    """
    check_credentials_are_declared(get_registered(provider))


@pytest.mark.parametrize("provider", registered_providers())
def test_declared_transports_build(provider: AIProvider) -> None:
    """Every declared transport builds; every undeclared one is rejected.

    Args:
        provider: The provider under test.
    """
    check_declared_transports_build(get_registered(provider))


@pytest.mark.parametrize("provider", registered_providers())
async def test_aclose_is_idempotent(provider: AIProvider) -> None:
    """Every built provider closes cleanly on a repeated call.

    Args:
        provider: The provider under test.
    """
    await check_aclose_is_idempotent(get_registered(provider))


@pytest.mark.parametrize("provider", registered_providers())
def test_config_model_validates_documented_examples(provider: AIProvider) -> None:
    """Every plugin's block model accepts the block the docs publish for it.

    Args:
        provider: The provider under test.
    """
    plugin = get_registered(provider)
    check_config_model_validates_documented_examples(
        plugin,
        documented_provider_examples().get(provider.value, ()),
    )


@pytest.mark.parametrize("provider", registered_providers())
def test_cli_contract_id_resolves(provider: AIProvider) -> None:
    """Every declared CLI contract id resolves to that plugin's own contract.

    Args:
        provider: The provider under test.
    """
    check_cli_contract_id_resolves(get_registered(provider))


def test_display_names_are_unique() -> None:
    """No two registered providers present themselves under one name."""
    load_builtin_providers()

    check_display_names_are_unique(all_providers())


def test_every_provider_is_documented() -> None:
    """The docs publish an ``ai.providers.<name>`` block for each provider.

    Without this the ``config_model`` check would silently degrade to
    validating ``{}`` for a provider whose documented block was deleted.
    """
    documented = set(documented_provider_examples())

    assert_that(documented).contains(
        *[provider.value for provider in registered_providers()],
    )


class _NoCloseProvider:
    """What a plugin returns when it never inherited the lifecycle contract.

    Deliberately not a :class:`~lintro.ai.providers.base.BaseAIProvider`: it
    has no ``aclose``, which is the omission
    :func:`check_aclose_is_idempotent` exists to catch.
    """


def fake_metadata(
    *,
    display_name: str = "Fake",
    default_model: str = "fake-model",
    default_api_key_env: str = "FAKE_API_KEY",
    cli_contract_id: str | None = None,
    cli_auth_probe: CliAuthProbe | None = None,
    pricing: Mapping[str, ModelPricing] | None = None,
) -> ProviderMetadata:
    """Build metadata for an incomplete plugin, defective where asked.

    Registration keys on :attr:`AIProvider.ANTHROPIC` because a plugin's name
    must be a real enum member; the record never reaches the live registry.

    Args:
        display_name: Vendor name, blanked by the metadata negative test.
        default_model: Default model identifier.
        default_api_key_env: API-key variable, blanked by the credentials
            negative test.
        cli_contract_id: Contract key, pointed at nothing by the contract-id
            negative test.
        cli_auth_probe: Declared auth probe, if any.
        pricing: Pricing table, emptied by the pricing negative test.

    Returns:
        A metadata record describing an API-only fake provider.
    """
    return ProviderMetadata(
        provider=AIProvider.ANTHROPIC,
        display_name=display_name,
        default_model=default_model,
        default_api_key_env=default_api_key_env,
        supported_transports=frozenset({AITransport.API}),
        default_transport=AITransport.API,
        sdk_package="fake-sdk",
        cli_contract_id=cli_contract_id,
        cli_auth_probe=cli_auth_probe,
        pricing={"fake-model": ModelPricing(1.0, 2.0)} if pricing is None else pricing,
    )


@dataclass(frozen=True, kw_only=True)
class FakeIncompleteProvider:
    """A provider plugin that breaks the contract on purpose.

    Satisfies the shape of
    :class:`~lintro.ai.providers.protocol.ProviderPlugin` well enough to be
    passed to the ``check_*`` functions, and no further: it builds an object
    with no ``aclose``, accepts transports it never declared, and can be told
    to fail the build it does declare.

    Attributes:
        metadata: The (possibly defective) static description.
        transports: Transports this fake claims to serve.
        config_model: Model for this fake's ``ai.providers.<name>`` block.
        build_fails: Whether ``build`` raises instead of returning.
        built: Configs this plugin was asked to build, in call order.
    """

    metadata: ProviderMetadata = field(default_factory=fake_metadata)
    transports: frozenset[AITransport] = frozenset({AITransport.API})
    config_model: type[ProviderConfig] = ProviderConfig
    build_fails: bool = False
    built: list[AIConfig] = field(default_factory=list)

    @property
    def name(self) -> AIProvider:
        """Return the registry key this fake would register under.

        Returns:
            The provider its metadata describes.
        """
        return self.metadata.provider

    def build(self, config: AIConfig) -> BaseAIProvider:
        """Build the fake provider, or fail if the fixture asked it to.

        Args:
            config: Effective AI configuration for this run.

        Returns:
            An object that is not a provider and cannot be closed.

        Raises:
            RuntimeError: When ``build_fails`` is set.
        """
        self.built.append(config)
        if self.build_fails:
            raise RuntimeError("no client")
        return _NoCloseProvider()  # type: ignore[return-value]


@pytest.fixture()
def incomplete_provider() -> Callable[..., FakeIncompleteProvider]:
    """Return a factory for :class:`FakeIncompleteProvider` instances.

    Returns:
        A callable taking the same keyword arguments as
        :class:`FakeIncompleteProvider`.
    """

    def _make(**overrides: Any) -> FakeIncompleteProvider:
        """Build one incomplete plugin.

        Args:
            **overrides: Fields to override on the fake.

        Returns:
            The configured fake plugin.
        """
        return FakeIncompleteProvider(**overrides)

    return _make


def test_metadata_check_fails_on_a_blank_field(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """A blank metadata string fails the metadata check, naming the field.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugin = incomplete_provider(metadata=fake_metadata(display_name="  "))

    with pytest.raises(AssertionError) as excinfo:
        check_metadata_fields_are_populated(plugin)

    assert_that(str(excinfo.value)).contains("metadata.display_name is blank")


def test_metadata_check_fails_when_a_transport_field_is_unset(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """Declaring API transport without an SDK distribution is reported.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    metadata = ProviderMetadata(
        provider=AIProvider.ANTHROPIC,
        display_name="Fake",
        default_model="fake-model",
        default_api_key_env="FAKE_API_KEY",
        supported_transports=frozenset({AITransport.API}),
        default_transport=AITransport.API,
        pricing={"fake-model": ModelPricing(1.0, 2.0)},
    )

    with pytest.raises(AssertionError) as excinfo:
        check_metadata_fields_are_populated(incomplete_provider(metadata=metadata))

    assert_that(str(excinfo.value)).contains("metadata.sdk_package is unset")


def test_pricing_check_fails_on_an_empty_table(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """A provider that prices no model at all is reported.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugin = incomplete_provider(metadata=fake_metadata(pricing={}))

    with pytest.raises(AssertionError) as excinfo:
        check_pricing_declares_a_priced_model(plugin)

    assert_that(str(excinfo.value)).contains("declares no model")


def test_credentials_check_fails_without_a_key_env_or_probe(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """Neither an API-key variable nor an auth probe is a contract breach.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugin = incomplete_provider(
        metadata=fake_metadata(default_api_key_env="", cli_auth_probe=None),
    )

    with pytest.raises(AssertionError) as excinfo:
        check_credentials_are_declared(plugin)

    assert_that(str(excinfo.value)).contains(
        "declares neither default_api_key_env nor cli_auth_probe",
    )


def test_credentials_check_passes_on_a_probe_alone(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """An explicit auth probe satisfies the credentials rule on its own.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugin = incomplete_provider(
        metadata=fake_metadata(
            default_api_key_env="",
            cli_auth_probe=CliAuthProbe(
                configured_message="configured",
                unverified_message="unverified",
                hint="log in",
            ),
        ),
    )

    check_credentials_are_declared(plugin)


def test_transport_check_fails_when_a_declared_transport_cannot_build(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """A plugin that declares a transport it cannot construct is reported.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugin = incomplete_provider(build_fails=True)

    with pytest.raises(AssertionError) as excinfo:
        check_declared_transports_build(plugin)

    assert_that(str(excinfo.value)).contains(
        "declares transport 'api' but build() raised RuntimeError",
    )


def test_transport_check_fails_when_an_undeclared_transport_is_accepted(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """Building a transport the plugin never declared is reported.

    The fake declares no transport at all, so the very first one asked for is
    undeclared — and its ``build`` answers anyway, which is the drift a plugin
    that quietly outgrew its declaration would show.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugin = incomplete_provider(transports=frozenset())

    with pytest.raises(AssertionError) as excinfo:
        check_declared_transports_build(plugin)

    assert_that(str(excinfo.value)).contains(
        "does not declare transport 'api' but build() accepted it",
    )


async def test_aclose_check_fails_when_the_built_provider_has_none(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """A built object with no ``aclose`` fails the lifecycle check.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugin = incomplete_provider()

    with pytest.raises(AssertionError) as excinfo:
        await check_aclose_is_idempotent(plugin)

    assert_that(str(excinfo.value)).contains("has no aclose()")


def test_config_model_check_fails_on_a_rejected_documented_example(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """A block model that rejects its own documented example is reported.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugin = incomplete_provider()

    with pytest.raises(AssertionError) as excinfo:
        check_config_model_validates_documented_examples(
            plugin,
            [{"cli_bare": "auto"}],
        )

    assert_that(str(excinfo.value)).contains("config_model rejects its documented")


def test_display_name_check_fails_on_a_collision(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """Two providers presenting one display name are reported.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugins = {
        AIProvider.ANTHROPIC: incomplete_provider(),
        AIProvider.OPENAI: incomplete_provider(),
    }

    with pytest.raises(AssertionError) as excinfo:
        check_display_names_are_unique(plugins)

    assert_that(str(excinfo.value)).contains("is claimed by both")


def test_cli_contract_check_fails_on_an_unresolvable_id(
    incomplete_provider: Callable[..., FakeIncompleteProvider],
) -> None:
    """A contract id naming no declared contract is reported.

    Args:
        incomplete_provider: Factory for the incomplete plugin.
    """
    plugin = incomplete_provider(
        metadata=fake_metadata(cli_contract_id="not-a-contract"),
    )

    with pytest.raises(AssertionError) as excinfo:
        check_cli_contract_id_resolves(plugin)

    assert_that(str(excinfo.value)).contains("resolves to no declared CLI contract")
