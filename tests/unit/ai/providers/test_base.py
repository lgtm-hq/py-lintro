"""Tests for base AI provider."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.providers.base import AIResponse, BaseAIProvider


class TestAIResponse:
    """Tests for AIResponse dataclass."""

    def test_defaults(self):
        resp = AIResponse(content="hello", model="test")
        assert_that(resp.content).is_equal_to("hello")
        assert_that(resp.model).is_equal_to("test")
        assert_that(resp.input_tokens).is_equal_to(0)
        assert_that(resp.output_tokens).is_equal_to(0)
        assert_that(resp.cost_estimate).is_equal_to(0.0)
        assert_that(resp.provider).is_equal_to("")

    def test_with_all_fields(self):
        resp = AIResponse(
            content="test",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.005,
            provider="openai",
        )
        assert_that(resp.input_tokens).is_equal_to(100)
        assert_that(resp.cost_estimate).is_equal_to(0.005)


class TestBaseAIProvider:
    """Tests for BaseAIProvider ABC."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseAIProvider()

    def test_subclass_must_implement(self):
        class IncompleteProvider(BaseAIProvider):
            pass

        with pytest.raises(TypeError):
            IncompleteProvider()

    def test_complete_subclass(self):
        class TestProvider(BaseAIProvider):
            def complete(
                self,
                prompt,
                *,
                system=None,
                max_tokens=1024,
            ):
                return AIResponse(
                    content="ok",
                    model="test",
                )

            def is_available(self):
                return True

            @property
            def name(self):
                return "test"

            @property
            def model_name(self):
                return "test-model"

        provider = TestProvider()
        assert_that(provider.name).is_equal_to("test")
        assert_that(provider.model_name).is_equal_to("test-model")
        assert_that(provider.is_available()).is_true()

        result = provider.complete("hello")
        assert_that(result.content).is_equal_to("ok")
