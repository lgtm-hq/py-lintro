"""Tests for the read-only AI config view dataclasses."""

from __future__ import annotations

import dataclasses

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.config_views import (
    AIBudgetConfig,
    AIOutputConfig,
    AIProviderConfig,
)
from lintro.ai.enums import AITransport, ConfidenceLevel, SanitizeMode
from lintro.ai.registry import AIProvider


def test_provider_config_construction_and_fields() -> None:
    """AIProviderConfig stores provider-related fields verbatim."""
    view = AIProviderConfig(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.API,
        model="claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        api_base_url=None,
        api_region=None,
        fallback_models=("gpt-4o",),
        max_tokens=2048,
        max_retries=2,
        api_timeout=60.0,
        retry_base_delay=1.0,
        retry_max_delay=10.0,
        retry_backoff_factor=2.0,
    )

    assert_that(view.provider).is_equal_to(AIProvider.ANTHROPIC)
    assert_that(view.transport).is_equal_to(AITransport.API)
    assert_that(view.model).is_equal_to("claude-sonnet-4-6")
    assert_that(view.fallback_models).is_equal_to(("gpt-4o",))
    assert_that(view.max_tokens).is_equal_to(2048)


def test_provider_config_is_frozen() -> None:
    """AIProviderConfig rejects mutation."""
    view = AIProviderConfig(
        provider=AIProvider.ANTHROPIC,
        transport=None,
        model=None,
        api_key_env=None,
        api_base_url=None,
        api_region=None,
        fallback_models=(),
        max_tokens=1,
        max_retries=0,
        api_timeout=1.0,
        retry_base_delay=1.0,
        retry_max_delay=1.0,
        retry_backoff_factor=1.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.model = "changed"  # type: ignore[misc]


def test_budget_config_construction_and_frozen() -> None:
    """AIBudgetConfig stores budget fields and is immutable."""
    view = AIBudgetConfig(
        max_fix_attempts=3,
        max_parallel_calls=4,
        max_cost_usd=1.5,
        max_prompt_tokens=8000,
        max_refinement_attempts=2,
        enable_cache=True,
        cache_ttl=3600,
        cache_max_entries=500,
        context_lines=3,
        fix_search_radius=5,
    )

    assert_that(view.max_fix_attempts).is_equal_to(3)
    assert_that(view.max_cost_usd).is_equal_to(1.5)
    assert_that(view.enable_cache).is_true()
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.max_fix_attempts = 99  # type: ignore[misc]


def test_output_config_construction_and_frozen() -> None:
    """AIOutputConfig stores output fields and is immutable."""
    view = AIOutputConfig(
        show_cost_estimate=True,
        verbose=False,
        stream=True,
        dry_run=False,
        github_pr_comments=False,
        validate_after_group=True,
        auto_apply=False,
        auto_apply_safe_fixes=True,
        default_fix=False,
        fail_on_ai_error=False,
        fail_on_unfixed=False,
        min_confidence=ConfidenceLevel.MEDIUM,
        sanitize_mode=SanitizeMode.WARN,
        include_paths=("src",),
        exclude_paths=(),
        include_rules=(),
        exclude_rules=("E501",),
    )

    assert_that(view.stream).is_true()
    assert_that(view.min_confidence).is_equal_to(ConfidenceLevel.MEDIUM)
    assert_that(view.sanitize_mode).is_equal_to(SanitizeMode.WARN)
    assert_that(view.include_paths).is_equal_to(("src",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.stream = False  # type: ignore[misc]


def test_views_derived_from_ai_config() -> None:
    """AIConfig exposes each grouped view with the expected types."""
    config = AIConfig()

    provider_view = config.provider_config
    budget_view = config.budget_config
    output_view = config.output_config

    assert_that(provider_view).is_instance_of(AIProviderConfig)
    assert_that(budget_view).is_instance_of(AIBudgetConfig)
    assert_that(output_view).is_instance_of(AIOutputConfig)
    assert_that(provider_view.provider).is_equal_to(config.provider)
