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


def test_exceptions_hierarchy():
    assert_that(AIError("x")).is_instance_of(LintroError)
    assert_that(AINotAvailableError("x")).is_instance_of(AIError)
    assert_that(AIProviderError("x")).is_instance_of(AIError)
    assert_that(AIAuthenticationError("x")).is_instance_of(AIProviderError)
    assert_that(AIRateLimitError("x")).is_instance_of(AIProviderError)
    assert_that(AITokenLimitError("x")).is_instance_of(AIError)
