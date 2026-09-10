"""Async retry decorator for AI API calls with exponential backoff.

Retries transient failures (network errors, rate limits) while
immediately propagating permanent failures (authentication errors).

The decorator wraps *coroutine functions*: backoff waits use
``asyncio.sleep`` so a retrying call never blocks the event loop or the
other AI calls running concurrently on it.
"""

from __future__ import annotations

import asyncio
import functools
import random
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)

# Defaults
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0
DEFAULT_BACKOFF_FACTOR = 2.0
# 429 gets its own, larger budget: a rate limit is a wait-and-succeed
# condition, unlike a 5xx or a socket error, and the server usually tells
# us exactly how long to wait (#2506).
DEFAULT_RATE_LIMIT_MAX_RETRIES = 6
RATE_LIMIT_EXHAUSTED_HINT = (
    "AI provider rate limit not cleared after {retries} retries; "
    "wait for the limit window to reset and rerun."
)


def _retry_delay(
    *,
    error: AIProviderError,
    attempt: int,
    base_delay: float,
    max_delay: float,
    backoff_factor: float,
) -> float:
    """Return the wait before the next attempt.

    A 429 carrying ``Retry-After`` is honoured verbatim: the provider named
    the instant its window resets, so jittering or truncating it only
    guarantees another 429 (#2506). Everything else uses the exponential
    backoff, jittered ±20 % to keep concurrent lintro processes from
    retrying in lockstep.

    Args:
        error: The transient failure being retried.
        attempt: Zero-based attempt index that just failed.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Maximum backoff delay in seconds.
        backoff_factor: Multiplier applied to delay after each attempt.

    Returns:
        Delay in seconds.
    """
    retry_after = getattr(error, "retry_after", None)
    if isinstance(error, AIRateLimitError) and retry_after is not None:
        return float(retry_after)
    delay = min(base_delay * (backoff_factor**attempt), max_delay)
    # Not used for security/cryptographic purposes.
    delay *= random.uniform(0.8, 1.2)  # nosec B311
    return min(delay, max_delay)


async def _sleep_before_retry(
    *,
    error: AIProviderError,
    attempt: int,
    budget: int,
    base_delay: float,
    max_delay: float,
    backoff_factor: float,
) -> None:
    """Log the pending retry and await its delay.

    Args:
        error: The transient failure being retried.
        attempt: Zero-based attempt index that just failed.
        budget: Retry budget the attempt is counted against.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Maximum backoff delay in seconds.
        backoff_factor: Multiplier applied to delay after each attempt.
    """
    delay = _retry_delay(
        error=error,
        attempt=attempt,
        base_delay=base_delay,
        max_delay=max_delay,
        backoff_factor=backoff_factor,
    )
    logger.debug(
        f"AI retry {attempt + 1}/{budget}: {error}, waiting {delay:.1f}s",
    )
    await asyncio.sleep(delay)


def with_retry(
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    rate_limit_max_retries: int = DEFAULT_RATE_LIMIT_MAX_RETRIES,
) -> Callable[
    [Callable[..., Awaitable[Any]]],
    Callable[..., Awaitable[Any]],
]:
    """Decorator for retrying AI API calls with exponential backoff and jitter.

    Wraps a coroutine function and returns a coroutine function.

    Retries on ``AIProviderError`` and ``AIRateLimitError``.
    Does NOT retry on ``AIAuthenticationError`` (permanent failure).

    Each retry delay is computed as ``min(base_delay * factor^attempt,
    max_delay)`` then jittered by ±20 % to avoid thundering-herd
    alignment when multiple processes retry concurrently.

    ``AIRateLimitError`` (HTTP 429) is handled apart from the other
    transient failures (#2506): it gets ``rate_limit_max_retries``
    attempts rather than ``max_retries``, counted on its own counter so
    neither budget can be spent by the other error type, and when the
    provider sent a
    ``Retry-After`` header the wait is exactly that value — unjittered,
    because the server named the instant its bucket refills. Exhausting
    the 429 budget raises an ``AIRateLimitError`` whose message names the
    rate limit and asks for a rerun.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Maximum delay in seconds between retries.
        backoff_factor: Multiplier applied to delay after each attempt.
        rate_limit_max_retries: Maximum retry attempts for HTTP 429 only.

    Returns:
        Decorated function with retry behavior.

    Raises:
        ValueError: If any retry parameter is invalid (negative or
            max_delay < base_delay).
    """
    if rate_limit_max_retries < 0:
        msg = "rate_limit_max_retries must be >= 0, got " f"{rate_limit_max_retries}"
        raise ValueError(msg)
    if max_retries < 0:
        msg = f"max_retries must be >= 0, got {max_retries}"
        raise ValueError(msg)
    if base_delay < 0:
        msg = f"base_delay must be >= 0, got {base_delay}"
        raise ValueError(msg)
    if max_delay < 0:
        msg = f"max_delay must be >= 0, got {max_delay}"
        raise ValueError(msg)
    if backoff_factor <= 0:
        msg = f"backoff_factor must be > 0, got {backoff_factor}"
        raise ValueError(msg)
    if max_delay < base_delay:
        msg = f"max_delay ({max_delay}) must be >= base_delay ({base_delay})"
        raise ValueError(msg)

    def decorator(
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        """Wrap *func* with the configured retry loop.

        Args:
            func: The coroutine function to wrap.

        Returns:
            A coroutine function with retry behavior.
        """

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            """Await the wrapped call, retrying transient failures.

            The two budgets are counted separately: a run of 5xx errors
            must not eat the 429 allowance, and one 429 must not spend
            the transient-failure allowance. Every branch returns,
            raises, or sleeps and increments exactly one counter, and
            both counters are bounded, so the loop always terminates.
            """
            rate_limit_retries = 0
            transient_retries = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except AIAuthenticationError:
                    raise  # Never retry auth errors
                except AIRateLimitError as e:
                    if rate_limit_retries >= rate_limit_max_retries:
                        raise AIRateLimitError(
                            RATE_LIMIT_EXHAUSTED_HINT.format(
                                retries=rate_limit_max_retries,
                            )
                            + f" Last provider error: {e}",
                            retry_after=e.retry_after,
                        ) from e
                    await _sleep_before_retry(
                        error=e,
                        attempt=rate_limit_retries,
                        budget=rate_limit_max_retries,
                        base_delay=base_delay,
                        max_delay=max_delay,
                        backoff_factor=backoff_factor,
                    )
                    rate_limit_retries += 1
                except AIProviderError as e:
                    if transient_retries >= max_retries:
                        raise
                    await _sleep_before_retry(
                        error=e,
                        attempt=transient_retries,
                        budget=max_retries,
                        base_delay=base_delay,
                        max_delay=max_delay,
                        backoff_factor=backoff_factor,
                    )
                    transient_retries += 1

        return wrapper

    return decorator
