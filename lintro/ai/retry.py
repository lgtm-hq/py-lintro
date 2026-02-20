"""Retry decorator for AI API calls with exponential backoff.

Retries transient failures (network errors, rate limits) while
immediately propagating permanent failures (authentication errors).
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
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
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for retrying AI API calls with exponential backoff.

    Retries on ``AIProviderError`` and ``AIRateLimitError``.
    Does NOT retry on ``AIAuthenticationError`` (permanent failure).

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Maximum delay in seconds between retries.
        backoff_factor: Multiplier applied to delay after each attempt.

    Returns:
        Decorated function with retry behavior.
    """

    def decorator(
        func: Callable[..., Any],
    ) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
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
                    logger.debug(
                        f"AI retry {attempt + 1}/{max_retries}: {e}, "
                        f"waiting {delay:.1f}s",
                    )
                    time.sleep(delay)
            raise last_exception  # type: ignore[misc]  # unreachable but satisfies type checker

        return wrapper

    return decorator
