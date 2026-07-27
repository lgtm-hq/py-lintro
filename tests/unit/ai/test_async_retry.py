"""Async-specific behaviour of the AI retry decorator.

Covers the properties that only exist once the retry loop is async:
backoff yields to the event loop instead of blocking it, retries of
concurrent calls interleave, and the "never retry authentication" rule
still holds when calls run concurrently.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.retry import with_retry


async def test_retry_backoff_yields_to_event_loop() -> None:
    """Verify a retrying call does not block other tasks on the loop."""
    ticks: list[str] = []

    @with_retry(max_retries=1, base_delay=0.05)
    async def flaky() -> str:
        """Fail once, then succeed.

        Returns:
            A fixed marker value.

        Raises:
            AIProviderError: On the first attempt.
        """
        if not ticks or ticks[0] != "other":
            ticks.append("retrying")
            raise AIProviderError("transient")
        return "ok"

    async def other() -> None:
        """Record that an unrelated task ran during the backoff wait."""
        await asyncio.sleep(0)
        ticks.insert(0, "other")

    results = await asyncio.gather(flaky(), other())
    assert_that(results[0]).is_equal_to("ok")
    assert_that(ticks).contains("other")


async def test_retry_backoff_does_not_block_the_thread() -> None:
    """Verify concurrent retrying calls wait in parallel, not in series."""
    attempts: dict[int, int] = {}

    @with_retry(max_retries=1, base_delay=0.2)
    async def flaky(key: int) -> str:
        """Fail once per key, then succeed.

        Args:
            key: Identifier for the concurrent call.

        Returns:
            A fixed marker value.

        Raises:
            AIRateLimitError: On the first attempt for each key.
        """
        attempts[key] = attempts.get(key, 0) + 1
        if attempts[key] == 1:
            raise AIRateLimitError("slow down")
        return "ok"

    started = time.monotonic()
    results = await asyncio.gather(*(flaky(key) for key in range(4)))
    elapsed = time.monotonic() - started

    assert_that(results).is_equal_to(["ok"] * 4)
    # Serial blocking sleeps would need ~0.8s; concurrent waits need ~0.2s.
    assert_that(elapsed).is_less_than(0.6)


async def test_auth_error_is_never_retried_under_concurrency() -> None:
    """Verify AIAuthenticationError fails fast even with concurrent callers."""
    calls: list[int] = []

    @with_retry(max_retries=3, base_delay=0.01)
    async def unauthorized(key: int) -> str:
        """Always fail with an authentication error.

        Args:
            key: Identifier for the concurrent call.

        Returns:
            Never returns.

        Raises:
            AIAuthenticationError: Always.
        """
        calls.append(key)
        raise AIAuthenticationError("bad key")

    outcomes = await asyncio.gather(
        *(unauthorized(key) for key in range(3)),
        return_exceptions=True,
    )

    assert_that(calls).is_length(3)
    for outcome in outcomes:
        assert_that(outcome).is_instance_of(AIAuthenticationError)


async def test_retry_wrapper_is_a_coroutine_function() -> None:
    """Verify the decorator returns an awaitable wrapper, not a sync callable."""

    @with_retry(max_retries=0)
    async def fn() -> str:
        """Succeed immediately.

        Returns:
            A fixed marker value.
        """
        return "ok"

    assert_that(asyncio.iscoroutinefunction(fn)).is_true()
    assert_that(await fn()).is_equal_to("ok")


async def test_retry_propagates_cancellation() -> None:
    """Verify cancelling a retrying call is not swallowed as a failure."""

    @with_retry(max_retries=5, base_delay=10.0)
    async def always_failing() -> str:
        """Always fail so the wrapper enters its backoff wait.

        Returns:
            Never returns.

        Raises:
            AIProviderError: Always.
        """
        raise AIProviderError("transient")

    task = asyncio.ensure_future(always_failing())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
