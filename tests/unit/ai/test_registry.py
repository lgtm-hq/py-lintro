"""Tests for the AI provider registry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict

import pytest
from assertpy import assert_that

from lintro.ai.registry import (
    DEFAULT_PRICING,
    PROVIDERS,
    AIProvider,
    ModelPricing,
    ProviderInfo,
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
    assert_that(members).is_length(2)
    assert_that(members).contains(AIProvider.ANTHROPIC, AIProvider.OPENAI)


def test_aiprovider_from_string():
    """AIProvider can be constructed from a plain string."""
    assert_that(AIProvider("anthropic")).is_equal_to(AIProvider.ANTHROPIC)
    assert_that(AIProvider("openai")).is_equal_to(AIProvider.OPENAI)


def test_aiprovider_invalid_value_raises():
    """Constructing AIProvider with an unknown value raises ValueError."""
    with pytest.raises(ValueError, match="not a valid"):
        AIProvider("gemini")


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


# -- ProviderInfo ----------------------------------------------------------


def test_provider_info_fields():
    """ProviderInfo stores all expected attributes."""
    info = ProviderInfo(
        default_model="test-model",
        default_api_key_env="TEST_KEY",
        models={"test-model": ModelPricing(1.0, 2.0)},
    )
    assert_that(info.default_model).is_equal_to("test-model")
    assert_that(info.default_api_key_env).is_equal_to("TEST_KEY")
    assert_that(info.models).contains_key("test-model")


def test_provider_info_default_models_empty():
    """ProviderInfo.models defaults to an empty dict."""
    info = ProviderInfo(default_model="m", default_api_key_env="K")
    assert_that(info.models).is_empty()


# -- AIProviderRegistry ----------------------------------------------------


def test_registry_items():
    """items() yields all providers."""
    items = list(PROVIDERS.items())
    assert_that(items).is_length(2)
    providers = [p for p, _ in items]
    assert_that(providers).contains(AIProvider.ANTHROPIC, AIProvider.OPENAI)


def test_registry_get():
    """get() returns the correct ProviderInfo."""
    info = PROVIDERS.get(AIProvider.ANTHROPIC)
    assert_that(info).is_same_as(PROVIDERS.anthropic)
    info = PROVIDERS.get(AIProvider.OPENAI)
    assert_that(info).is_same_as(PROVIDERS.openai)


def test_registry_model_pricing_contains_all_models():
    """model_pricing merges every model from all providers."""
    pricing = PROVIDERS.model_pricing
    for _provider, info in PROVIDERS.items():
        for model_name in info.models:
            assert_that(pricing).contains_key(model_name)


def test_registry_model_pricing_values_are_model_pricing():
    """Every value in model_pricing is a ModelPricing instance."""
    for _model, p in PROVIDERS.model_pricing.items():
        assert_that(p).is_instance_of(ModelPricing)


def test_registry_default_models():
    """default_models maps each AIProvider to a string."""
    defaults = PROVIDERS.default_models
    assert_that(defaults).contains_key(AIProvider.ANTHROPIC)
    assert_that(defaults).contains_key(AIProvider.OPENAI)
    for _provider, model in defaults.items():
        assert_that(model).is_instance_of(str)


def test_registry_default_api_key_envs():
    """default_api_key_envs maps each AIProvider to a string."""
    envs = PROVIDERS.default_api_key_envs
    assert_that(envs).contains_key(AIProvider.ANTHROPIC)
    assert_that(envs).contains_key(AIProvider.OPENAI)
    assert_that(envs[AIProvider.ANTHROPIC]).is_equal_to("ANTHROPIC_API_KEY")
    assert_that(envs[AIProvider.OPENAI]).is_equal_to("OPENAI_API_KEY")


def test_registry_default_model_in_provider_models():
    """Every default model exists in its provider's models dict."""
    for _provider, info in PROVIDERS.items():
        assert_that(info.models).contains_key(info.default_model)


# -- asdict ----------------------------------------------------------------


def test_asdict_produces_nested_dict():
    """asdict(PROVIDERS) produces a correct nested dictionary."""
    d = asdict(PROVIDERS)
    assert_that(d).contains_key("anthropic", "openai")
    anthropic_info = d["anthropic"]
    assert_that(anthropic_info).contains_key(
        "default_model",
        "default_api_key_env",
        "models",
    )
    # Models are nested dicts with pricing fields.
    for _model_name, pricing in anthropic_info["models"].items():
        assert_that(pricing).contains_key(
            "input_per_million",
            "output_per_million",
        )


# -- DEFAULT_PRICING -------------------------------------------------------


def test_default_pricing_is_model_pricing():
    """DEFAULT_PRICING is a ModelPricing instance."""
    assert_that(DEFAULT_PRICING).is_instance_of(ModelPricing)


def test_default_pricing_values():
    """DEFAULT_PRICING has expected fallback values."""
    assert_that(DEFAULT_PRICING.input_per_million).is_equal_to(3.00)
    assert_that(DEFAULT_PRICING.output_per_million).is_equal_to(15.00)
