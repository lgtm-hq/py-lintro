"""Tests for AI configuration."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from pydantic import ValidationError

from lintro.ai.config import AIConfig

# -- Defaults --------------------------------------------------------------


def test_default_config_booleans_and_provider() -> None:
    """All boolean defaults and provider are correct out of the box."""
    config = AIConfig()
    assert_that(config.enabled).is_false()
    assert_that(config.provider).is_equal_to("anthropic")
    assert_that(config.default_fix).is_false()
    assert_that(config.auto_apply).is_false()
    assert_that(config.auto_apply_safe_fixes).is_true()
    assert_that(config.validate_after_group).is_false()
    assert_that(config.show_cost_estimate).is_true()


def test_default_config_optional_fields() -> None:
    """Optional fields default to None."""
    config = AIConfig()
    assert_that(config.model).is_none()
    assert_that(config.api_key_env).is_none()


def test_default_config_numeric_fields() -> None:
    """All numeric fields have correct defaults."""
    config = AIConfig()
    assert_that(config.max_tokens).is_equal_to(4096)
    assert_that(config.max_fix_issues).is_equal_to(20)
    assert_that(config.max_parallel_calls).is_equal_to(5)
    assert_that(config.max_retries).is_equal_to(2)
    assert_that(config.api_timeout).is_equal_to(60.0)
    assert_that(config.context_lines).is_equal_to(15)
    assert_that(config.fix_search_radius).is_equal_to(5)
    assert_that(config.retry_base_delay).is_equal_to(1.0)
    assert_that(config.retry_max_delay).is_equal_to(30.0)
    assert_that(config.retry_backoff_factor).is_equal_to(2.0)


# -- Provider constraint ---------------------------------------------------


def test_provider_anthropic() -> None:
    """Provider 'anthropic' is accepted."""
    config = AIConfig(provider="anthropic")
    assert_that(config.provider).is_equal_to("anthropic")


def test_provider_openai() -> None:
    """Provider 'openai' is accepted."""
    config = AIConfig(provider="openai")
    assert_that(config.provider).is_equal_to("openai")


def test_provider_invalid_rejected() -> None:
    """An invalid provider string is rejected by validation."""
    with pytest.raises(ValidationError):
        AIConfig(provider="gemini")  # type: ignore[arg-type] -- intentionally invalid for validation test


# -- Boolean overrides -----------------------------------------------------


def test_enabled_override() -> None:
    """Enabled can be set to True."""
    config = AIConfig(enabled=True)
    assert_that(config.enabled).is_true()


def test_auto_apply_override() -> None:
    """auto_apply can be toggled."""
    config = AIConfig(auto_apply=True)
    assert_that(config.auto_apply).is_true()


def test_auto_apply_safe_fixes_override() -> None:
    """auto_apply_safe_fixes can be disabled."""
    config = AIConfig(auto_apply_safe_fixes=False)
    assert_that(config.auto_apply_safe_fixes).is_false()


def test_default_fix_override() -> None:
    """default_fix can be enabled."""
    config = AIConfig(default_fix=True)
    assert_that(config.default_fix).is_true()


def test_validate_after_group_override() -> None:
    """validate_after_group can be enabled."""
    config = AIConfig(validate_after_group=True)
    assert_that(config.validate_after_group).is_true()


# -- String fields ---------------------------------------------------------


def test_custom_model() -> None:
    """Custom model string is stored correctly."""
    config = AIConfig(model="gpt-4-turbo")
    assert_that(config.model).is_equal_to("gpt-4-turbo")


def test_custom_api_key_env() -> None:
    """Custom api_key_env is stored correctly."""
    config = AIConfig(api_key_env="MY_API_KEY")
    assert_that(config.api_key_env).is_equal_to("MY_API_KEY")


# -- max_tokens ------------------------------------------------------------


def test_max_tokens_accepts_minimum() -> None:
    """max_tokens accepts the minimum value of 1."""
    config = AIConfig(max_tokens=1)
    assert_that(config.max_tokens).is_equal_to(1)


def test_max_tokens_accepts_large_value() -> None:
    """max_tokens accepts large values (no upper bound)."""
    config = AIConfig(max_tokens=512000)
    assert_that(config.max_tokens).is_equal_to(512000)


def test_max_tokens_rejects_zero() -> None:
    """max_tokens=0 violates ge=1 constraint."""
    with pytest.raises(ValidationError):
        AIConfig(max_tokens=0)


# -- max_parallel_calls ----------------------------------------------------


def test_max_parallel_calls_accepts_bounds() -> None:
    """max_parallel_calls accepts boundary values 1 and 20."""
    assert_that(AIConfig(max_parallel_calls=1).max_parallel_calls).is_equal_to(1)
    assert_that(AIConfig(max_parallel_calls=20).max_parallel_calls).is_equal_to(20)


def test_max_parallel_calls_rejects_out_of_range() -> None:
    """max_parallel_calls rejects 0 and 21."""
    with pytest.raises(ValidationError):
        AIConfig(max_parallel_calls=0)
    with pytest.raises(ValidationError):
        AIConfig(max_parallel_calls=21)


# -- max_retries -----------------------------------------------------------


def test_max_retries_accepts_bounds() -> None:
    """max_retries accepts 0 (disabled) and 10 (max)."""
    assert_that(AIConfig(max_retries=0).max_retries).is_equal_to(0)
    assert_that(AIConfig(max_retries=10).max_retries).is_equal_to(10)


def test_max_retries_rejects_out_of_range() -> None:
    """max_retries rejects -1 and 11."""
    with pytest.raises(ValidationError):
        AIConfig(max_retries=-1)
    with pytest.raises(ValidationError):
        AIConfig(max_retries=11)


# -- api_timeout -----------------------------------------------------------


def test_api_timeout_accepts_minimum() -> None:
    """api_timeout accepts the minimum of 1.0."""
    config = AIConfig(api_timeout=1.0)
    assert_that(config.api_timeout).is_equal_to(1.0)


def test_api_timeout_accepts_large_value() -> None:
    """api_timeout accepts large values."""
    config = AIConfig(api_timeout=300.0)
    assert_that(config.api_timeout).is_equal_to(300.0)


def test_api_timeout_rejects_below_minimum() -> None:
    """api_timeout rejects values below 1.0."""
    with pytest.raises(ValidationError):
        AIConfig(api_timeout=0.5)


# -- context_lines ---------------------------------------------------------


def test_context_lines_accepts_bounds() -> None:
    """context_lines accepts boundary values 1 and 100."""
    assert_that(AIConfig(context_lines=1).context_lines).is_equal_to(1)
    assert_that(AIConfig(context_lines=100).context_lines).is_equal_to(100)


def test_context_lines_rejects_out_of_range() -> None:
    """context_lines rejects 0 and 101."""
    with pytest.raises(ValidationError):
        AIConfig(context_lines=0)
    with pytest.raises(ValidationError):
        AIConfig(context_lines=101)


# -- fix_search_radius -----------------------------------------------------


def test_fix_search_radius_accepts_bounds() -> None:
    """fix_search_radius accepts boundary values 1 and 50."""
    assert_that(AIConfig(fix_search_radius=1).fix_search_radius).is_equal_to(1)
    assert_that(AIConfig(fix_search_radius=50).fix_search_radius).is_equal_to(50)


def test_fix_search_radius_rejects_out_of_range() -> None:
    """fix_search_radius rejects 0 and 51."""
    with pytest.raises(ValidationError):
        AIConfig(fix_search_radius=0)
    with pytest.raises(ValidationError):
        AIConfig(fix_search_radius=51)


# -- retry_base_delay ------------------------------------------------------


def test_retry_base_delay_accepts_minimum() -> None:
    """retry_base_delay accepts the minimum of 0.1."""
    config = AIConfig(retry_base_delay=0.1)
    assert_that(config.retry_base_delay).is_equal_to(0.1)


def test_retry_base_delay_rejects_below_minimum() -> None:
    """retry_base_delay rejects values below 0.1."""
    with pytest.raises(ValidationError):
        AIConfig(retry_base_delay=0.05)


# -- retry_max_delay -------------------------------------------------------


def test_retry_max_delay_accepts_minimum() -> None:
    """retry_max_delay accepts the minimum of 1.0."""
    config = AIConfig(retry_max_delay=1.0)
    assert_that(config.retry_max_delay).is_equal_to(1.0)


def test_retry_max_delay_rejects_below_minimum() -> None:
    """retry_max_delay rejects values below 1.0."""
    with pytest.raises(ValidationError):
        AIConfig(retry_max_delay=0.5)


# -- retry_backoff_factor --------------------------------------------------


def test_retry_backoff_factor_accepts_minimum() -> None:
    """retry_backoff_factor accepts the minimum of 1.0."""
    config = AIConfig(retry_backoff_factor=1.0)
    assert_that(config.retry_backoff_factor).is_equal_to(1.0)


def test_retry_backoff_factor_rejects_below_minimum() -> None:
    """retry_backoff_factor rejects values below 1.0."""
    with pytest.raises(ValidationError):
        AIConfig(retry_backoff_factor=0.5)


# -- Extra fields forbidden ------------------------------------------------


def test_extra_fields_forbidden() -> None:
    """Unknown fields are rejected by the model."""
    with pytest.raises(ValidationError):
        AIConfig(unknown_field="value")  # type: ignore[call-arg] -- intentionally invalid for validation test
