"""Runtime model fallback chain for AI providers.

When a primary model fails with a retryable error, the fallback chain
tries each configured fallback model in order before giving up.
Authentication errors are never retried.
"""

from __future__ import annotations

from loguru import logger

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.providers.base import AIResponse, BaseAIProvider


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
                    "Fallback chain: trying model '{}' (attempt {}/{})",
                    label,
                    idx + 1,
                    len(models_to_try),
                )
                return provider.complete(
                    prompt,
                    system=system,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            except AIAuthenticationError:
                # Never retry auth errors — restore and propagate.
                raise
            except (AIProviderError, AIRateLimitError) as exc:
                last_error = exc
                if idx < len(models_to_try) - 1:
                    next_model = models_to_try[idx + 1]
                    logger.debug(
                        "Fallback chain: model '{}' failed ({}), "
                        "falling back to '{}'",
                        label,
                        exc,
                        next_model,
                    )
                else:
                    logger.debug(
                        "Fallback chain: model '{}' failed ({}), " "no more fallbacks",
                        label,
                        exc,
                    )
    finally:
        provider.model_name = original_model

    # All models exhausted — raise the last error.
    # last_error is always an AIProviderError or AIRateLimitError here
    # (AIAuthenticationError is re-raised immediately in the loop).
    if isinstance(last_error, AIRateLimitError):
        raise AIRateLimitError(str(last_error)) from last_error
    raise AIProviderError(
        str(last_error) if last_error else "Fallback chain exhausted",
    ) from last_error
