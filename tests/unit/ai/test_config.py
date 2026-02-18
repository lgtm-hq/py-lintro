"""Tests for AI configuration."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from pydantic import ValidationError

from lintro.ai.config import AIConfig


class TestAIConfig:
    """Tests for AIConfig model."""

    def test_default_config(self):
        config = AIConfig()
        assert_that(config.enabled).is_false()
        assert_that(config.provider).is_equal_to("anthropic")
        assert_that(config.model).is_none()
        assert_that(config.api_key_env).is_none()
        assert_that(config.auto_apply).is_false()
        assert_that(config.max_tokens).is_equal_to(4096)
        assert_that(config.show_cost_estimate).is_true()

    def test_enabled_config(self):
        config = AIConfig(enabled=True, provider="openai")
        assert_that(config.enabled).is_true()
        assert_that(config.provider).is_equal_to("openai")

    def test_custom_model(self):
        config = AIConfig(model="gpt-4-turbo")
        assert_that(config.model).is_equal_to("gpt-4-turbo")

    def test_custom_api_key_env(self):
        config = AIConfig(api_key_env="MY_API_KEY")
        assert_that(config.api_key_env).is_equal_to("MY_API_KEY")

    def test_auto_apply(self):
        config = AIConfig(auto_apply=True)
        assert_that(config.auto_apply).is_true()

    def test_max_tokens_bounds(self):
        config = AIConfig(max_tokens=1)
        assert_that(config.max_tokens).is_equal_to(1)

        config = AIConfig(max_tokens=128000)
        assert_that(config.max_tokens).is_equal_to(128000)

    def test_max_tokens_too_low(self):
        with pytest.raises(ValidationError):
            AIConfig(max_tokens=0)

    def test_max_tokens_too_high(self):
        with pytest.raises(ValidationError):
            AIConfig(max_tokens=128001)

    def test_default_fix(self):
        config = AIConfig()
        assert_that(config.default_fix).is_false()

        config = AIConfig(default_fix=True)
        assert_that(config.default_fix).is_true()

    def test_max_parallel_calls_default(self):
        config = AIConfig()
        assert_that(config.max_parallel_calls).is_equal_to(5)

    def test_max_parallel_calls_bounds(self):
        config = AIConfig(max_parallel_calls=1)
        assert_that(config.max_parallel_calls).is_equal_to(1)

        config = AIConfig(max_parallel_calls=20)
        assert_that(config.max_parallel_calls).is_equal_to(20)

        with pytest.raises(ValidationError):
            AIConfig(max_parallel_calls=0)

        with pytest.raises(ValidationError):
            AIConfig(max_parallel_calls=21)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AIConfig(unknown_field="value")
