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


def with_retry(
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
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

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Maximum delay in seconds between retries.
        backoff_factor: Multiplier applied to delay after each attempt.

    Returns:
        Decorated function with retry behavior.

    Raises:
        ValueError: If any retry parameter is invalid (negative or
            max_delay < base_delay).
    """
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
            """Await the wrapped call, retrying transient failures."""
            last_exception: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except AIAuthenticationError:
                    raise  # Never retry auth errors
                except (AIProviderError, AIRateLimitError) as e:
                    last_exception = e
                    if attempt == max_retries:
                        raise
                    delay = min(
                        base_delay * (backoff_factor**attempt),
                        max_delay,
                    )
                    # Jitter ±20% to prevent thundering-herd alignment
                    # across concurrent lintro processes. Not used for
                    # security/cryptographic purposes.
                    delay *= random.uniform(0.8, 1.2)  # nosec B311
                    delay = min(delay, max_delay)
                    logger.debug(
                        f"AI retry {attempt + 1}/{max_retries}: {e}, "
                        f"waiting {delay:.1f}s",
                    )
                    await asyncio.sleep(delay)
            assert (
                last_exception is not None
            ), "Retry loop exhausted without capturing an exception"
            raise last_exception

        return wrapper

    return decorator
