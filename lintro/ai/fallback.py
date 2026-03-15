"""Runtime model fallback chain for AI providers.

When a primary model fails with a retryable error, the fallback chain
tries each configured fallback model in order before giving up.
Authentication errors are never retried.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from loguru import logger

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.providers.base import AIResponse, AIStreamResult, BaseAIProvider

_T = TypeVar("_T")


def _with_fallback(
    provider: BaseAIProvider,
    attempt_fn: Callable[[str, str | None, int, float], _T],
    prompt: str,
    *,
    fallback_models: list[str] | None = None,
    system: str | None = None,
    max_tokens: int = 1024,
    timeout: float = 60.0,
    label_prefix: str = "Fallback chain",
) -> _T:
    """Run *attempt_fn* with automatic model fallback.

    Tries the provider's current (primary) model first. On
    ``AIProviderError`` or ``AIRateLimitError``, swaps to each fallback
    model in order and retries. ``AIAuthenticationError`` is never
    retried — it propagates immediately.

    **Mutation contract:** the provider's ``model_name`` is temporarily
    mutated to each fallback model during retries, but is always restored
    to its original value — even on success, on error, or if an
    ``AIAuthenticationError`` short-circuits the chain.

    Args:
        provider: AI provider instance whose ``model_name`` may be
            temporarily mutated during retries.
        attempt_fn: Callable with signature
            ``(prompt, system, max_tokens, timeout) -> T``. Typically
            ``provider.complete`` or ``provider.stream_complete``.
        prompt: The user prompt.
        fallback_models: Ordered list of fallback model identifiers.
            When empty or ``None``, behaves identically to a single
            call to *attempt_fn*.
        system: Optional system prompt.
        max_tokens: Maximum tokens to generate.
        timeout: Request timeout in seconds.
        label_prefix: Prefix for debug log messages.

    Returns:
        The first successful result from *attempt_fn*.

    Raises:
        AIAuthenticationError: Immediately on authentication failure.
        AIProviderError: If the primary model and all fallbacks fail.
        AIRateLimitError: If the primary model and all fallbacks fail
            with rate-limit errors (last error is re-raised).
    """
    models_to_try: list[str | None] = [None]  # None = keep current model
    if fallback_models:
        models_to_try.extend(fallback_models)

    original_model = provider.model_name
    last_error: Exception | None = None

    try:
        for idx, model in enumerate(models_to_try):
            if model is not None:
                provider.model_name = model

            label = provider.model_name
            try:
                logger.debug(
                    "{}: trying model '{}' (attempt {}/{})",
                    label_prefix,
                    label,
                    idx + 1,
                    len(models_to_try),
                )
                return attempt_fn(prompt, system, max_tokens, timeout)
            except AIAuthenticationError:
                # Never retry auth errors — restore and propagate.
                raise
            except (AIProviderError, AIRateLimitError) as exc:
                last_error = exc
                if idx < len(models_to_try) - 1:
                    next_model = models_to_try[idx + 1]
                    logger.debug(
                        "{}: model '{}' failed ({}), falling back to '{}'",
                        label_prefix,
                        label,
                        exc,
                        next_model,
                    )
                else:
                    logger.debug(
                        "{}: model '{}' failed ({}), no more fallbacks",
                        label_prefix,
                        label,
                        exc,
                    )
    finally:
        provider.model_name = original_model

    # All models exhausted — raise the last error.
    if isinstance(last_error, AIRateLimitError):
        raise AIRateLimitError(str(last_error)) from last_error
    raise AIProviderError(
        str(last_error) if last_error else f"{label_prefix} exhausted",
    ) from last_error


def complete_with_fallback(
    provider: BaseAIProvider,
    prompt: str,
    *,
    fallback_models: list[str] | None = None,
    system: str | None = None,
    max_tokens: int = 1024,
    timeout: float = 60.0,
) -> AIResponse:
    """Call ``provider.complete()`` with automatic model fallback.

    Tries the provider's current (primary) model first. On
    ``AIProviderError`` or ``AIRateLimitError``, swaps to each fallback
    model in order and retries. ``AIAuthenticationError`` is never
    retried — it propagates immediately.

    After all attempts (successful or not), the provider's ``model_name``
    is restored to the original value.

    Args:
        provider: AI provider instance.
        prompt: The user prompt.
        fallback_models: Ordered list of fallback model identifiers.
            When empty or ``None``, behaves identically to a plain
            ``provider.complete()`` call.
        system: Optional system prompt.
        max_tokens: Maximum tokens to generate.
        timeout: Request timeout in seconds.

    Returns:
        The first successful ``AIResponse``.
    """

    def _attempt(
        prompt: str,
        system: str | None,
        max_tokens: int,
        timeout: float,
    ) -> AIResponse:
        return provider.complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    return _with_fallback(
        provider,
        _attempt,
        prompt,
        fallback_models=fallback_models,
        system=system,
        max_tokens=max_tokens,
        timeout=timeout,
        label_prefix="Fallback chain",
    )


def stream_complete_with_fallback(
    provider: BaseAIProvider,
    prompt: str,
    *,
    fallback_models: list[str] | None = None,
    system: str | None = None,
    max_tokens: int = 1024,
    timeout: float = 60.0,
) -> AIStreamResult:
    """Call ``provider.stream_complete()`` with automatic model fallback.

    Same fallback logic as ``complete_with_fallback`` but returns a
    streaming result. Only retries on setup failures — once a provider
    begins yielding tokens, its stream is returned directly.

    Args:
        provider: AI provider instance.
        prompt: The user prompt.
        fallback_models: Ordered list of fallback model identifiers.
        system: Optional system prompt.
        max_tokens: Maximum tokens to generate.
        timeout: Request timeout in seconds.

    Returns:
        The first successful ``AIStreamResult``.
    """

    def _attempt(
        prompt: str,
        system: str | None,
        max_tokens: int,
        timeout: float,
    ) -> AIStreamResult:
        return provider.stream_complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    return _with_fallback(
        provider,
        _attempt,
        prompt,
        fallback_models=fallback_models,
        system=system,
        max_tokens=max_tokens,
        timeout=timeout,
        label_prefix="Stream fallback",
    )
