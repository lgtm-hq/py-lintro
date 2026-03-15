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
    """Provider 'anthropic' is accepted (Pydantic coerces str to AIProvider)."""
    config = AIConfig(provider="anthropic")  # type: ignore[arg-type]  # tests YAML-style str coercion
    assert_that(config.provider).is_equal_to("anthropic")


def test_provider_openai() -> None:
    """Provider 'openai' is accepted (Pydantic coerces str to AIProvider)."""
    config = AIConfig(provider="openai")  # type: ignore[arg-type]  # tests YAML-style str coercion
    assert_that(config.provider).is_equal_to("openai")


def test_provider_invalid_rejected() -> None:
    """An invalid provider string is rejected by validation."""
    with pytest.raises(ValidationError):
        AIConfig(provider="gemini")  # type: ignore[arg-type]  # intentionally invalid


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


# -- Numeric field boundary values (parametrized) --------------------------


@pytest.mark.parametrize(
    ("field_name", "valid_value", "expected"),
    [
        ("max_tokens", 1, 1),
        ("max_tokens", 128000, 128000),
        ("max_parallel_calls", 1, 1),
        ("max_parallel_calls", 20, 20),
        ("max_retries", 0, 0),
        ("max_retries", 10, 10),
        ("api_timeout", 1.0, 1.0),
        ("api_timeout", 300.0, 300.0),
        ("context_lines", 1, 1),
        ("context_lines", 100, 100),
        ("fix_search_radius", 1, 1),
        ("fix_search_radius", 50, 50),
        ("retry_base_delay", 0.1, 0.1),
        ("retry_max_delay", 1.0, 1.0),
        ("retry_backoff_factor", 1.0, 1.0),
    ],
    ids=[
        "max_tokens=1",
        "max_tokens=128000",
        "max_parallel_calls=1",
        "max_parallel_calls=20",
        "max_retries=0",
        "max_retries=10",
        "api_timeout=1.0",
        "api_timeout=300.0",
        "context_lines=1",
        "context_lines=100",
        "fix_search_radius=1",
        "fix_search_radius=50",
        "retry_base_delay=0.1",
        "retry_max_delay=1.0",
        "retry_backoff_factor=1.0",
    ],
)
def test_numeric_field_accepts_valid_value(
    field_name: str,
    valid_value: int | float,
    expected: int | float,
) -> None:
    """Numeric field {field_name} accepts value {valid_value}."""
    config = AIConfig(**{field_name: valid_value})  # type: ignore[arg-type]  # dynamic field name
    assert_that(getattr(config, field_name)).is_equal_to(expected)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_tokens", 0),
        ("max_parallel_calls", 0),
        ("max_parallel_calls", 21),
        ("max_retries", -1),
        ("max_retries", 11),
        ("api_timeout", 0.5),
        ("context_lines", 0),
        ("context_lines", 101),
        ("fix_search_radius", 0),
        ("fix_search_radius", 51),
        ("retry_base_delay", 0.05),
        ("retry_max_delay", 0.5),
        ("retry_backoff_factor", 0.5),
    ],
    ids=[
        "max_tokens=0",
        "max_parallel_calls=0",
        "max_parallel_calls=21",
        "max_retries=-1",
        "max_retries=11",
        "api_timeout=0.5",
        "context_lines=0",
        "context_lines=101",
        "fix_search_radius=0",
        "fix_search_radius=51",
        "retry_base_delay=0.05",
        "retry_max_delay=0.5",
        "retry_backoff_factor=0.5",
    ],
)
def test_numeric_field_rejects_invalid_value(
    field_name: str,
    invalid_value: int | float,
) -> None:
    """Numeric field {field_name} rejects invalid value {invalid_value}."""
    with pytest.raises(ValidationError):
        AIConfig(**{field_name: invalid_value})  # type: ignore[arg-type]  # dynamic field name


# -- Cross-field validators ------------------------------------------------


def test_retry_max_delay_less_than_base_raises() -> None:
    """retry_max_delay < retry_base_delay raises ValidationError."""
    with pytest.raises(ValidationError, match="retry_max_delay"):
        AIConfig(retry_base_delay=5.0, retry_max_delay=1.0)


# -- Extra fields forbidden ------------------------------------------------


def test_extra_fields_forbidden() -> None:
    """Unknown fields are rejected by the model."""
    with pytest.raises(ValidationError):
        AIConfig(unknown_field="value")  # type: ignore[call-arg]  # intentionally invalid
