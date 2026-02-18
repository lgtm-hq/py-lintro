"""Tests for AI retry decorator."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.retry import with_retry


class TestWithRetry:
    """Tests for the with_retry decorator."""

    def test_succeeds_on_first_attempt(self):
        call_count = 0

        @with_retry(max_retries=3)
        def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = fn()
        assert_that(result).is_equal_to("ok")
        assert_that(call_count).is_equal_to(1)

    @patch("lintro.ai.retry.time.sleep")
    def test_retries_on_provider_error(self, mock_sleep):
        call_count = 0

        @with_retry(max_retries=3, base_delay=1.0)
        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise AIProviderError("server error")
            return "ok"

        result = fn()
        assert_that(result).is_equal_to("ok")
        assert_that(call_count).is_equal_to(3)
        assert_that(mock_sleep.call_count).is_equal_to(2)

    @patch("lintro.ai.retry.time.sleep")
    def test_retries_on_rate_limit_error(self, mock_sleep):
        call_count = 0

        @with_retry(max_retries=2, base_delay=1.0)
        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise AIRateLimitError("rate limited")
            return "ok"

        result = fn()
        assert_that(result).is_equal_to("ok")
        assert_that(call_count).is_equal_to(2)

    def test_does_not_retry_on_authentication_error(self):
        call_count = 0

        @with_retry(max_retries=3)
        def fn():
            nonlocal call_count
            call_count += 1
            raise AIAuthenticationError("bad key")

        with pytest.raises(AIAuthenticationError):
            fn()
        assert_that(call_count).is_equal_to(1)

    @patch("lintro.ai.retry.time.sleep")
    def test_raises_after_max_retries_exhausted(self, mock_sleep):
        @with_retry(max_retries=2, base_delay=0.1)
        def fn():
            raise AIProviderError("always fails")

        with pytest.raises(AIProviderError, match="always fails"):
            fn()
        assert_that(mock_sleep.call_count).is_equal_to(2)

    @patch("lintro.ai.retry.time.sleep")
    def test_exponential_backoff_delays(self, mock_sleep):
        call_count = 0

        @with_retry(max_retries=3, base_delay=1.0, backoff_factor=2.0)
        def fn():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise AIProviderError("fail")
            return "ok"

        result = fn()
        assert_that(result).is_equal_to("ok")

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert_that(delays).is_equal_to([1.0, 2.0, 4.0])

    @patch("lintro.ai.retry.time.sleep")
    def test_max_delay_cap(self, mock_sleep):
        call_count = 0

        @with_retry(
            max_retries=5,
            base_delay=10.0,
            backoff_factor=3.0,
            max_delay=25.0,
        )
        def fn():
            nonlocal call_count
            call_count += 1
            if call_count <= 5:
                raise AIProviderError("fail")
            return "ok"

        fn()
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        # All delays should be capped at 25.0
        for delay in delays:
            assert_that(delay).is_less_than_or_equal_to(25.0)

    def test_does_not_retry_non_ai_exceptions(self):
        call_count = 0

        @with_retry(max_retries=3)
        def fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("not an AI error")

        with pytest.raises(ValueError):
            fn()
        assert_that(call_count).is_equal_to(1)

    def test_preserves_function_metadata(self):
        @with_retry(max_retries=1)
        def my_function():
            """My docstring."""
            return 42

        assert_that(my_function.__name__).is_equal_to("my_function")
        assert_that(my_function.__doc__).is_equal_to("My docstring.")
