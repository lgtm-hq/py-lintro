"""Tests for the provider-metadata facade."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIProviderNotRegisteredError
from lintro.ai.provider_enum import (
    accepted_provider_values,
    provider_required_error,
)
from lintro.ai.providers.base import BaseAIProvider
from lintro.ai.providers.protocol import ProviderMetadata
from lintro.ai.providers.registry import (
    all_providers,
    clear_registered,
    register_provider,
    restore_registered,
)
from lintro.ai.registry import (
    DEFAULT_PRICING,
    AIProvider,
    ModelPricing,
    all_metadata,
    default_api_key_envs,
    default_models,
    metadata_for,
    model_pricing,
)


@dataclass(frozen=True, slots=True)
class _FakeAnthropicPlugin:
    """A plugin that registers under Anthropic with obviously fake metadata."""

    @property
    def name(self) -> AIProvider:
        """Return the registry key this plugin claims.

        Returns:
            :attr:`AIProvider.ANTHROPIC`.
        """
        return AIProvider.ANTHROPIC

    @property
    def transports(self) -> frozenset[AITransport]:
        """Return the transports this fake serves.

        Returns:
            The API transport only.
        """
        return self.metadata.supported_transports

    @property
    def metadata(self) -> ProviderMetadata:
        """Return the fake description.

        Returns:
            A minimal record naming a model no real provider ships.
        """
        return ProviderMetadata(
            provider=AIProvider.ANTHROPIC,
            display_name="Fake",
            default_model="fake-model",
            default_api_key_env="FAKE_API_KEY",
            supported_transports=frozenset({AITransport.API}),
            default_transport=AITransport.API,
            sdk_package="fake-sdk",
            pricing={"fake-model": ModelPricing(1.0, 2.0)},
        )

    def build(self, config: AIConfig) -> BaseAIProvider:
        """Refuse to build; no test in this module constructs a provider.

        Args:
            config: Ignored.

        Returns:
            Never returns.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError


# -- AIProvider StrEnum ----------------------------------------------------


def test_aiprovider_members():
    """All expected members exist with lowercase string values."""
    assert_that(AIProvider.ANTHROPIC).is_equal_to("anthropic")
    assert_that(AIProvider.OPENAI).is_equal_to("openai")


def test_aiprovider_is_str():
    """StrEnum members are str instances."""
    for member in AIProvider:
        assert_that(member).is_instance_of(str)


def test_aiprovider_iteration():
    """Iterating AIProvider yields all members."""
    members = list(AIProvider)
    assert_that(len(members)).is_greater_than_or_equal_to(2)
    assert_that(members).contains(AIProvider.ANTHROPIC, AIProvider.OPENAI)


def test_aiprovider_from_string():
    """AIProvider can be constructed from a plain string."""
    assert_that(AIProvider("anthropic")).is_equal_to(AIProvider.ANTHROPIC)
    assert_that(AIProvider("openai")).is_equal_to(AIProvider.OPENAI)


def test_aiprovider_invalid_value_raises():
    """Constructing AIProvider with an unknown value raises ValueError."""
    with pytest.raises(ValueError, match="not a valid"):
        AIProvider("gemini")


def test_accepted_provider_values_are_alphabetical() -> None:
    """User-visible provider lists carry no ranking."""
    assert_that(accepted_provider_values()).is_equal_to("anthropic, cursor, openai")


def test_provider_required_error_names_the_three_set_paths() -> None:
    """The migration error names config, env, and flag, then accepted values."""
    message = provider_required_error()
    assert_that(message).contains("`ai.provider` in config")
    assert_that(message).contains("LINTRO_AI_PROVIDER")
    assert_that(message).contains("--provider")
    assert_that(message).contains("anthropic, cursor, openai")


# -- ModelPricing ----------------------------------------------------------


def test_model_pricing_fields():
    """ModelPricing stores input and output rates."""
    p = ModelPricing(input_per_million=3.00, output_per_million=15.00)
    assert_that(p.input_per_million).is_equal_to(3.00)
    assert_that(p.output_per_million).is_equal_to(15.00)


def test_model_pricing_frozen():
    """ModelPricing is immutable."""
    p = ModelPricing(1.0, 2.0)
    with pytest.raises(FrozenInstanceError):
        p.input_per_million = 999.0  # type: ignore[misc]


# -- Facade ---------------------------------------------------------------


def test_all_metadata_covers_every_provider() -> None:
    """Every enum member resolves to a registered plugin's metadata."""
    metadata = all_metadata()
    assert_that(list(metadata)).is_equal_to(list(AIProvider))
    for provider, record in metadata.items():
        assert_that(record.provider).is_equal_to(provider)


@pytest.mark.parametrize("provider", list(AIProvider))
@pytest.mark.parametrize("spelling", [str.lower, str.upper, str.title])
def test_metadata_for_accepts_enum_and_any_casing(
    provider: AIProvider,
    spelling: Callable[[str], str],
) -> None:
    """A user-typed provider name resolves to the same record as the enum.

    Value equality, not identity: a plugin that returned an equal copy of its
    record each call would still satisfy every caller.

    Args:
        provider: The provider under test.
        spelling: Casing a user might type for ``--provider``.
    """
    assert_that(metadata_for(spelling(provider.value))).is_equal_to(
        metadata_for(provider),
    )


def test_metadata_for_rejects_a_known_provider_with_no_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recognised name with nothing registered is an error, not a blank record.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    saved = all_providers()
    # The facade imports the loader at call time, so the stub belongs on the
    # module that defines it.
    monkeypatch.setattr(
        "lintro.ai.providers.builtins.load_builtin_providers",
        lambda: None,
    )
    try:
        clear_registered()
        with pytest.raises(AIProviderNotRegisteredError) as excinfo:
            metadata_for(AIProvider.ANTHROPIC)
    finally:
        restore_registered(saved)

    assert_that(str(excinfo.value)).contains("anthropic", "no registered plugin")


@pytest.mark.parametrize("name", ["", "   ", "gemini"])
def test_metadata_for_rejects_an_unusable_name(name: str) -> None:
    """Blank and unknown names raise rather than resolving to something.

    Args:
        name: A name no provider answers to.
    """
    with pytest.raises(AIProviderNotRegisteredError):
        metadata_for(name)


def test_facade_loads_the_builtin_plugins_itself() -> None:
    """Lookups work on a cold registry; no caller has to load plugins first.

    The tables this facade replaced needed no registration, so a lookup that
    silently answered ``None`` until :func:`get_provider` had run once would be
    a regression that only shows up out of order.
    """
    from lintro.ai.providers import get_default_model
    from lintro.ai.providers.cli_contract_check import declared_cli_providers

    saved = all_providers()
    try:
        clear_registered()
        assert_that(list(all_metadata())).is_equal_to(list(AIProvider))
        clear_registered()
        assert_that(get_default_model("Anthropic")).is_equal_to("claude-sonnet-4-6")
        clear_registered()
        assert_that(list(declared_cli_providers())).is_equal_to(list(AIProvider))
    finally:
        restore_registered(saved)


def test_get_default_model_returns_none_for_an_unknown_provider() -> None:
    """An unrecognised name is None, not an exception, for the status renderer."""
    from lintro.ai.providers import get_default_model

    assert_that(get_default_model("gemini")).is_none()


def test_model_pricing_merges_every_provider() -> None:
    """model_pricing() is the union of every provider's pricing table."""
    pricing = model_pricing()
    for record in all_metadata().values():
        for model_name in record.pricing:
            assert_that(pricing).contains_key(model_name)
    for entry in pricing.values():
        assert_that(entry).is_instance_of(ModelPricing)


def test_default_models_are_priced_by_their_own_provider() -> None:
    """Every default model is one the same provider publishes pricing for."""
    for provider, model in default_models().items():
        assert_that(model).is_instance_of(str)
        assert_that(all_metadata()[provider].pricing).contains_key(model)


def test_default_api_key_envs() -> None:
    """Each provider declares the API-key variable it has always used."""
    envs = default_api_key_envs()
    assert_that(envs[AIProvider.ANTHROPIC]).is_equal_to("ANTHROPIC_API_KEY")
    assert_that(envs[AIProvider.OPENAI]).is_equal_to("OPENAI_API_KEY")
    assert_that(envs[AIProvider.CURSOR]).is_equal_to("CURSOR_API_KEY")


def test_facade_projections_carry_the_plugins_own_values() -> None:
    """The derived mappings restate metadata fields rather than re-deriving."""
    for provider, record in all_metadata().items():
        assert_that(default_models()[provider]).is_equal_to(record.default_model)
        assert_that(default_api_key_envs()[provider]).is_equal_to(
            record.default_api_key_env,
        )
        for name, pricing in record.pricing.items():
            assert_that(model_pricing()[name]).is_equal_to(pricing)


def test_facade_reflects_a_swapped_plugin() -> None:
    """Lookups read the registry live, so a swapped plugin is picked up.

    A module-level snapshot — the shape #2308 removed — would keep answering
    with the plugin that was registered first.
    """
    saved = all_providers()
    try:
        clear_registered()
        register_provider(_FakeAnthropicPlugin())
        assert_that(metadata_for(AIProvider.ANTHROPIC).default_model).is_equal_to(
            "fake-model",
        )
        assert_that(model_pricing()).contains_key("fake-model")
        # The other two are re-registered by the loader from their already
        # imported packages, so the facade still answers for every provider.
        assert_that(list(all_metadata())).is_equal_to(list(AIProvider))
    finally:
        restore_registered(saved)

    assert_that(metadata_for(AIProvider.ANTHROPIC).default_model).is_not_equal_to(
        "fake-model",
    )


def test_metadata_for_names_the_accepted_providers_when_rejecting() -> None:
    """The unknown-provider error is actionable, listing what is accepted."""
    with pytest.raises(AIProviderNotRegisteredError) as excinfo:
        metadata_for("gemini")

    assert_that(str(excinfo.value)).contains("gemini", "anthropic, cursor, openai")


# -- DEFAULT_PRICING -------------------------------------------------------


def test_default_pricing_is_model_pricing():
    """DEFAULT_PRICING is a ModelPricing instance."""
    assert_that(DEFAULT_PRICING).is_instance_of(ModelPricing)


def test_default_pricing_values():
    """DEFAULT_PRICING has expected fallback values."""
    assert_that(DEFAULT_PRICING.input_per_million).is_equal_to(3.00)
    assert_that(DEFAULT_PRICING.output_per_million).is_equal_to(15.00)
