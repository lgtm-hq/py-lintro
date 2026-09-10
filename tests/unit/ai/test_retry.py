"""Tests for the async AI retry decorator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.retry import with_retry


async def test_retry_succeeds_on_first_attempt() -> None:
    """Verify decorated function returns immediately on success without retrying."""
    call_count = 0

    @with_retry(max_retries=3)
    async def fn() -> str:
        """Succeed immediately.

        Returns:
            A fixed marker value.
        """
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await fn()
    assert_that(result).is_equal_to("ok")
    assert_that(call_count).is_equal_to(1)


@patch("lintro.ai.retry.asyncio.sleep")
async def test_retry_retries_on_provider_error(mock_sleep: MagicMock) -> None:
    """Verify AIProviderError triggers retries until success.

    Args:
        mock_sleep: Patched ``asyncio.sleep``.
    """
    call_count = 0

    @with_retry(max_retries=3, base_delay=1.0)
    async def fn() -> str:
        """Fail twice, then succeed.

        Returns:
            A fixed marker value.

        Raises:
            AIProviderError: On the first two attempts.
        """
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise AIProviderError("server error")
        return "ok"

    result = await fn()
    assert_that(result).is_equal_to("ok")
    assert_that(call_count).is_equal_to(3)
    assert_that(mock_sleep.call_count).is_equal_to(2)


@patch("lintro.ai.retry.asyncio.sleep")
async def test_retry_retries_on_rate_limit_error(mock_sleep: MagicMock) -> None:
    """Verify AIRateLimitError triggers retries until success.

    Args:
        mock_sleep: Patched ``asyncio.sleep``.
    """
    call_count = 0

    @with_retry(max_retries=2, base_delay=1.0)
    async def fn() -> str:
        """Fail once with a rate limit, then succeed.

        Returns:
            A fixed marker value.

        Raises:
            AIRateLimitError: On the first attempt.
        """
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise AIRateLimitError("rate limited")
        return "ok"

    result = await fn()
    assert_that(result).is_equal_to("ok")
    assert_that(call_count).is_equal_to(2)
    mock_sleep.assert_called_once()


async def test_retry_does_not_retry_on_authentication_error() -> None:
    """Verify AIAuthenticationError is raised immediately without retrying."""
    call_count = 0

    @with_retry(max_retries=3)
    async def fn() -> str:
        """Always fail with an authentication error.

        Returns:
            Never returns.

        Raises:
            AIAuthenticationError: Always.
        """
        nonlocal call_count
        call_count += 1
        raise AIAuthenticationError("bad key")

    with pytest.raises(AIAuthenticationError):
        await fn()
    assert_that(call_count).is_equal_to(1)


async def test_retry_raises_after_max_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original error surfaces after every retry attempt is spent.

    Args:
        monkeypatch: Pytest monkeypatch fixture, used to record backoff waits
            instead of really sleeping.
    """
    delays: list[float] = []
    attempts: list[int] = []

    async def _record_sleep(seconds: float) -> None:
        """Record a backoff wait without spending real time.

        Args:
            seconds: Requested backoff duration.
        """
        delays.append(seconds)

    monkeypatch.setattr("lintro.ai.retry.asyncio.sleep", _record_sleep)

    @with_retry(max_retries=2, base_delay=0.1)
    async def fn() -> str:
        """Always fail with a provider error.

        Returns:
            Never returns.

        Raises:
            AIProviderError: Always.
        """
        attempts.append(len(attempts) + 1)
        raise AIProviderError("always fails")

    with pytest.raises(AIProviderError, match="always fails"):
        await fn()

    # Three attempts (the original plus two retries) separated by two waits.
    assert_that(attempts).is_length(3)
    assert_that(delays).is_length(2)


@patch("lintro.ai.retry.random.uniform", return_value=1.0)
@patch("lintro.ai.retry.asyncio.sleep")
async def test_retry_exponential_backoff_delays(
    mock_sleep: MagicMock,
    _mock_uniform: MagicMock,
) -> None:
    """Verify retry delays follow exponential backoff progression.

    Args:
        mock_sleep: Patched ``asyncio.sleep``.
        _mock_uniform: Patched jitter source pinned to 1.0.
    """
    call_count = 0

    @with_retry(max_retries=3, base_delay=1.0, backoff_factor=2.0)
    async def fn() -> str:
        """Fail three times, then succeed.

        Returns:
            A fixed marker value.

        Raises:
            AIProviderError: On the first three attempts.
        """
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise AIProviderError("fail")
        return "ok"

    result = await fn()
    assert_that(result).is_equal_to("ok")

    delays = [call.args[0] for call in mock_sleep.call_args_list]
    assert_that(delays).is_equal_to([1.0, 2.0, 4.0])


async def test_retry_max_delay_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify retry delays are capped at the configured max_delay value.

    Args:
        monkeypatch: Pytest monkeypatch fixture, used to pin the jitter and
            record backoff waits instead of really sleeping.
    """
    delays: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        """Record a backoff wait without spending real time.

        Args:
            seconds: Requested backoff duration.
        """
        delays.append(seconds)

    monkeypatch.setattr("lintro.ai.retry.asyncio.sleep", _record_sleep)
    monkeypatch.setattr("lintro.ai.retry.random.uniform", lambda *_args: 1.0)

    call_count = 0

    @with_retry(
        max_retries=5,
        base_delay=10.0,
        backoff_factor=3.0,
        max_delay=25.0,
    )
    async def fn() -> str:
        """Fail five times, then succeed.

        Returns:
            A fixed marker value.

        Raises:
            AIProviderError: On the first five attempts.
        """
        nonlocal call_count
        call_count += 1
        if call_count <= 5:
            raise AIProviderError("fail")
        return "ok"

    result = await fn()

    assert_that(result).is_equal_to("ok")
    # The 10 -> 30 -> 90 ... growth is clamped to the 25s ceiling.
    assert_that(delays).is_equal_to([10.0, 25.0, 25.0, 25.0, 25.0])


async def test_retry_does_not_retry_non_ai_exceptions() -> None:
    """Verify non-AI exceptions propagate immediately without retrying."""
    call_count = 0

    @with_retry(max_retries=3)
    async def fn() -> str:
        """Always fail with a non-AI exception.

        Returns:
            Never returns.

        Raises:
            ValueError: Always.
        """
        nonlocal call_count
        call_count += 1
        raise ValueError("not an AI error")

    with pytest.raises(ValueError, match="not an AI error"):
        await fn()
    assert_that(call_count).is_equal_to(1)


def test_retry_preserves_function_metadata() -> None:
    """Verify the retry decorator preserves the wrapped function name and docstring."""

    @with_retry(max_retries=1)
    async def my_function() -> int:
        """My docstring.

        Returns:
            A fixed value.
        """
        return 42

    assert_that(my_function.__name__).is_equal_to("my_function")
    assert_that(my_function.__doc__).starts_with("My docstring.")


@patch("lintro.ai.retry.asyncio.sleep")
async def test_retry_after_replaces_the_backoff_on_429(mock_sleep: MagicMock) -> None:
    """A 429 carrying ``Retry-After: 3`` waits exactly 3 s, then retries (#2506).

    Args:
        mock_sleep: Patched ``asyncio.sleep``.
    """
    call_count = 0

    @with_retry(max_retries=3, base_delay=30.0, max_delay=60.0)
    async def fn() -> str:
        """Rate limit once with a server-advertised wait, then succeed.

        Returns:
            A fixed marker value.

        Raises:
            AIRateLimitError: On the first attempt.
        """
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise AIRateLimitError("429", retry_after=3.0)
        return "ok"

    result = await fn()
    assert_that(result).is_equal_to("ok")
    assert_that(call_count).is_equal_to(2)
    mock_sleep.assert_awaited_once_with(3.0)


@patch("lintro.ai.retry.asyncio.sleep")
async def test_rate_limit_uses_its_own_larger_budget(mock_sleep: MagicMock) -> None:
    """429 retries run to ``rate_limit_max_retries``, not ``max_retries``.

    Args:
        mock_sleep: Patched ``asyncio.sleep``.
    """
    call_count = 0

    @with_retry(max_retries=1, rate_limit_max_retries=4, base_delay=0.1)
    async def fn() -> str:
        """Rate limit three times, then succeed.

        Returns:
            A fixed marker value.

        Raises:
            AIRateLimitError: On the first three attempts.
        """
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise AIRateLimitError("429")
        return "ok"

    assert_that(await fn()).is_equal_to("ok")
    assert_that(call_count).is_equal_to(4)
    assert_that(mock_sleep.await_count).is_equal_to(3)


@patch("lintro.ai.retry.asyncio.sleep")
async def test_exhausted_rate_limit_asks_for_a_rerun(mock_sleep: MagicMock) -> None:
    """Exhausting the 429 budget names the rate limit and asks to rerun.

    Args:
        mock_sleep: Patched ``asyncio.sleep``.
    """
    call_count = 0

    @with_retry(max_retries=5, rate_limit_max_retries=2, base_delay=0.1)
    async def fn() -> str:
        """Always rate limit.

        Returns:
            Never returns.

        Raises:
            AIRateLimitError: Always.
        """
        nonlocal call_count
        call_count += 1
        raise AIRateLimitError("Anthropic rate limit exceeded: 429")

    with pytest.raises(AIRateLimitError) as excinfo:
        await fn()

    message = str(excinfo.value)
    assert_that(message).contains("rate limit")
    assert_that(message).contains("rerun")
    assert_that(call_count).is_equal_to(3)
    assert_that(mock_sleep.await_count).is_equal_to(2)


@patch("lintro.ai.retry.asyncio.sleep")
async def test_non_rate_limit_keeps_the_default_budget(mock_sleep: MagicMock) -> None:
    """A 5xx still stops at ``max_retries`` even with a larger 429 budget.

    Args:
        mock_sleep: Patched ``asyncio.sleep``.
    """
    call_count = 0

    @with_retry(
        max_retries=1,
        rate_limit_max_retries=6,
        base_delay=2.0,
        backoff_factor=2.0,
    )
    async def fn() -> str:
        """Always fail with a transient provider error.

        Returns:
            Never returns.

        Raises:
            AIProviderError: Always.
        """
        nonlocal call_count
        call_count += 1
        raise AIProviderError("server error")

    with pytest.raises(AIProviderError):
        await fn()

    assert_that(call_count).is_equal_to(2)
    assert_that(mock_sleep.await_count).is_equal_to(1)
    # Backoff, not a Retry-After: jittered ±20 % around base_delay.
    slept = mock_sleep.await_args_list[0].args[0]
    assert_that(slept).is_between(1.6, 2.4)


def test_rate_limit_budget_must_not_be_negative() -> None:
    """A negative 429 budget is rejected at decoration time."""
    with pytest.raises(ValueError, match="rate_limit_max_retries"):
        with_retry(rate_limit_max_retries=-1)
