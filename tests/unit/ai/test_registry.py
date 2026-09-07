"""Tests for the provider-metadata facade."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from assertpy import assert_that

from lintro.ai.provider_enum import (
    accepted_provider_values,
    provider_required_error,
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


def test_metadata_for_accepts_enum_and_string() -> None:
    """A user-typed provider name resolves to the same record as the enum."""
    assert_that(metadata_for("anthropic")).is_same_as(
        metadata_for(AIProvider.ANTHROPIC),
    )


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


def test_facade_lookups_are_not_cached_snapshots() -> None:
    """Each call rebuilds from the registry rather than a module-level table."""
    first = all_metadata()
    second = all_metadata()
    assert_that(first).is_equal_to(second)
    assert_that(first).is_not_same_as(second)


# -- DEFAULT_PRICING -------------------------------------------------------


def test_default_pricing_is_model_pricing():
    """DEFAULT_PRICING is a ModelPricing instance."""
    assert_that(DEFAULT_PRICING).is_instance_of(ModelPricing)


def test_default_pricing_values():
    """DEFAULT_PRICING has expected fallback values."""
    assert_that(DEFAULT_PRICING.input_per_million).is_equal_to(3.00)
    assert_that(DEFAULT_PRICING.output_per_million).is_equal_to(15.00)
