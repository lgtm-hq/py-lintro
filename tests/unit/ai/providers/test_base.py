"""Tests for base AI provider."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.providers.base import AIResponse, BaseAIProvider


def test_ai_response_defaults():
    """Verify that AIResponse fields default to zero or empty when not provided."""
    resp = AIResponse(content="hello", model="test")
    assert_that(resp.content).is_equal_to("hello")
    assert_that(resp.model).is_equal_to("test")
    assert_that(resp.input_tokens).is_equal_to(0)
    assert_that(resp.output_tokens).is_equal_to(0)
    assert_that(resp.cost_estimate).is_equal_to(0.0)
    assert_that(resp.provider).is_equal_to("")


def test_ai_response_with_all_fields():
    """Verify that AIResponse stores all explicitly provided field values."""
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


def test_base_ai_provider_complete_subclass():
    """A complete BaseAIProvider subclass can be instantiated."""

    class TestProvider(BaseAIProvider):
        def complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            max_tokens: int = 1024,
            timeout: float = 60.0,
        ) -> AIResponse:
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


def test_base_ai_provider_cannot_instantiate_directly():
    """BaseAIProvider is abstract and cannot be instantiated."""
    import pytest

    with pytest.raises(TypeError):
        BaseAIProvider()  # type: ignore[abstract]  # intentionally testing abstract


def test_incomplete_subclass_fails():
    """A subclass missing abstract methods cannot be instantiated."""
    import pytest

    class IncompleteProvider(BaseAIProvider):
        def is_available(self):
            return True

    with pytest.raises(TypeError):
        IncompleteProvider()  # type: ignore[abstract]  # intentionally testing abstract
