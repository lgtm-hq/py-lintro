"""Tests for AI exceptions."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIError,
    AINotAvailableError,
    AIProviderError,
    AIRateLimitError,
    AITokenLimitError,
)
from lintro.exceptions.errors import LintroError


class TestExceptionHierarchy:
    """Tests for AI exception inheritance."""

    def test_ai_error_inherits_lintro_error(self):
        assert_that(AIError).is_type_of(type)
        err = AIError("test")
        assert_that(err).is_instance_of(LintroError)

    def test_not_available_inherits_ai_error(self):
        err = AINotAvailableError("test")
        assert_that(err).is_instance_of(AIError)

    def test_provider_error_inherits_ai_error(self):
        err = AIProviderError("test")
        assert_that(err).is_instance_of(AIError)

    def test_auth_error_inherits_provider_error(self):
        err = AIAuthenticationError("test")
        assert_that(err).is_instance_of(AIProviderError)

    def test_rate_limit_inherits_provider_error(self):
        err = AIRateLimitError("test")
        assert_that(err).is_instance_of(AIProviderError)

    def test_token_limit_inherits_ai_error(self):
        err = AITokenLimitError("test")
        assert_that(err).is_instance_of(AIError)

    def test_all_have_message(self):
        for exc_cls in [
            AIError,
            AINotAvailableError,
            AIProviderError,
            AIAuthenticationError,
            AIRateLimitError,
            AITokenLimitError,
        ]:
            err = exc_cls("test message")
            assert_that(str(err)).is_equal_to("test message")
