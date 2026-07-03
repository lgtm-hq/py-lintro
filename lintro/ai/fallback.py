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
    attempt_fn: Callable[[str, str | None, int, float, str], _T],
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
    ``AIProviderError`` or ``AIRateLimitError``, tries each fallback
    model in order and retries. ``AIAuthenticationError`` is never
    retried — it propagates immediately.

    Each attempt passes an explicit ``model`` override to *attempt_fn*
    so concurrent callers can share one provider instance safely.

    Args:
        provider: AI provider instance.
        attempt_fn: Callable with signature
            ``(prompt, system, max_tokens, timeout, model) -> T``.
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
            with rate-limit errors.
    """
    primary_model = provider.model_name
    models_to_try: list[str | None] = [None]
    if fallback_models:
        models_to_try.extend(fallback_models)

    last_error: Exception | None = None

    for idx, model_override in enumerate(models_to_try):
        effective_model = primary_model if model_override is None else model_override
        try:
            logger.debug(
                "{}: trying model '{}' (attempt {}/{})",
                label_prefix,
                effective_model,
                idx + 1,
                len(models_to_try),
            )
            return attempt_fn(
                prompt,
                system,
                max_tokens,
                timeout,
                effective_model,
            )
        except AIAuthenticationError:
            raise
        except (AIProviderError, AIRateLimitError) as exc:
            last_error = exc
            if idx < len(models_to_try) - 1:
                next_model = models_to_try[idx + 1]
                logger.debug(
                    "{}: model '{}' failed ({}), falling back to '{}'",
                    label_prefix,
                    effective_model,
                    exc,
                    next_model,
                )
            else:
                logger.debug(
                    "{}: model '{}' failed ({}), no more fallbacks",
                    label_prefix,
                    effective_model,
                    exc,
                )

    if isinstance(last_error, AIRateLimitError):
        raise AIRateLimitError(str(last_error)) from last_error
    if isinstance(last_error, AIProviderError):
        raise AIProviderError(str(last_error)) from last_error
    raise AIProviderError(f"{label_prefix} exhausted")


def complete_with_fallback(
    provider: BaseAIProvider,
    prompt: str,
    *,
    fallback_models: list[str] | None = None,
    system: str | None = None,
    max_tokens: int = 1024,
    timeout: float = 60.0,
    repo_root: str | None = None,
    use_one_shot: bool = False,
) -> AIResponse:
    """Call ``provider.complete()`` with automatic model fallback.

    Tries the provider's current (primary) model first. On
    ``AIProviderError`` or ``AIRateLimitError``, swaps to each fallback
    model in order and retries. ``AIAuthenticationError`` is never
    retried — it propagates immediately.

    Args:
        provider: AI provider instance.
        prompt: The user prompt.
        fallback_models: Ordered list of fallback model identifiers.
            When empty or ``None``, behaves identically to a plain
            ``provider.complete()`` call.
        system: Optional system prompt.
        max_tokens: Maximum tokens to generate.
        timeout: Request timeout in seconds.
        repo_root: Git repository root forwarded to the provider.
        use_one_shot: When True, skip durable session resume on the provider.

    Returns:
        The first successful ``AIResponse``.
    """

    def _attempt(
        prompt: str,
        system: str | None,
        max_tokens: int,
        timeout: float,
        model: str,
    ) -> AIResponse:
        return provider.complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
            repo_root=repo_root,
            use_one_shot=use_one_shot,
            model=model,
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
    streaming result. Fallback applies at stream *creation* time only —
    once a provider begins yielding tokens, mid-stream failures are
    not retried because partial content has already been consumed.

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
        model: str,
    ) -> AIStreamResult:
        return provider.stream_complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
            model=model,
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
