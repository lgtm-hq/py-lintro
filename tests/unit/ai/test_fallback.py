"""Tests for the runtime model fallback chain."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.fallback import _with_fallback, complete_with_fallback
from lintro.ai.providers.base import AIResponse


def _make_provider(model: str = "primary-model") -> MagicMock:
    """Create a mock provider with a configurable model_name attribute."""
    provider = MagicMock()
    provider.model_name = model
    return provider


def _ok_response(model: str = "primary-model") -> AIResponse:
    """Create a successful mock AI response."""
    return AIResponse(
        content="ok",
        model=model,
        input_tokens=10,
        output_tokens=5,
        cost_estimate=0.001,
        provider="mock",
    )


# -- TestCompleteWithFallbackPrimarySuccess: Primary model succeeds on first try.


def test_returns_response_without_fallback() -> None:
    """Return response when primary succeeds without fallback."""
    provider = _make_provider()
    provider.complete.return_value = _ok_response()

    result = complete_with_fallback(provider, "hello")

    assert_that(result.content).is_equal_to("ok")
    provider.complete.assert_called_once()


def test_returns_response_with_empty_fallback_list() -> None:
    """Return response when fallback list is empty."""
    provider = _make_provider()
    provider.complete.return_value = _ok_response()

    result = complete_with_fallback(provider, "hello", fallback_models=[])

    assert_that(result.content).is_equal_to("ok")
    provider.complete.assert_called_once()


def test_does_not_try_fallbacks_on_success() -> None:
    """Skip fallback models when primary succeeds."""
    provider = _make_provider()
    provider.complete.return_value = _ok_response()

    result = complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1", "fb-2"],
    )

    assert_that(result.content).is_equal_to("ok")
    assert_that(provider.complete.call_count).is_equal_to(1)
    # Model should be restored
    assert_that(provider.model_name).is_equal_to("primary-model")


# -- TestCompleteWithFallbackChain: Primary fails, fallback models are tried in order.


def test_falls_back_on_provider_error() -> None:
    """Fall back to next model on provider error."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIProviderError("primary down"),
        _ok_response("fb-1"),
    ]

    result = complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1"],
    )

    assert_that(result.content).is_equal_to("ok")
    assert_that(provider.complete.call_count).is_equal_to(2)
    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


def test_falls_back_on_rate_limit_error() -> None:
    """Fall back to next model on rate limit error."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIRateLimitError("rate limited"),
        _ok_response("fb-1"),
    ]

    result = complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1"],
    )

    assert_that(result.content).is_equal_to("ok")
    assert_that(provider.complete.call_count).is_equal_to(2)


def test_tries_multiple_fallbacks_in_order() -> None:
    """Try fallback models sequentially until one succeeds."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIProviderError("primary down"),
        AIRateLimitError("fb-1 rate limited"),
        _ok_response("fb-2"),
    ]

    result = complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1", "fb-2"],
    )

    assert_that(result.content).is_equal_to("ok")
    assert_that(provider.complete.call_count).is_equal_to(3)
    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


def test_model_is_swapped_for_each_fallback() -> None:
    """Verify each fallback attempt passes the expected model override."""
    provider = _make_provider("primary")
    models_seen: list[str] = []

    def capture_model(*args, **kwargs):
        """Record the per-call model and fail until the third call."""
        models_seen.append(kwargs["model"])
        if len(models_seen) < 3:
            raise AIProviderError("fail")
        return _ok_response(kwargs["model"])

    provider.complete.side_effect = capture_model

    complete_with_fallback(
        provider,
        "hello",
        fallback_models=["fb-1", "fb-2"],
    )

    assert_that(models_seen).is_equal_to(["primary", "fb-1", "fb-2"])
    assert_that(provider.model_name).is_equal_to("primary")


# -- TestCompleteWithFallbackAllFail: All models fail -- last error is raised.


def test_raises_last_error_when_all_fail() -> None:
    """Raise the last error when all models fail."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIProviderError("primary down"),
        AIRateLimitError("fb-1 limited"),
        AIProviderError("fb-2 down"),
    ]

    with pytest.raises(AIProviderError, match="fb-2 down"):
        complete_with_fallback(
            provider,
            "hello",
            fallback_models=["fb-1", "fb-2"],
        )

    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


def test_raises_primary_error_when_no_fallbacks() -> None:
    """Raise the primary error when no fallbacks are configured."""
    provider = _make_provider()
    provider.complete.side_effect = AIProviderError("primary down")

    with pytest.raises(AIProviderError, match="primary down"):
        complete_with_fallback(provider, "hello")


# -- TestCompleteWithFallbackAuthError: AIAuthenticationError is never retried.


def test_auth_error_propagates_immediately() -> None:
    """Propagate authentication error without trying fallbacks."""
    provider = _make_provider()
    provider.complete.side_effect = AIAuthenticationError("bad key")

    with pytest.raises(AIAuthenticationError, match="bad key"):
        complete_with_fallback(
            provider,
            "hello",
            fallback_models=["fb-1", "fb-2"],
        )

    # Only one call -- no fallback attempted
    assert_that(provider.complete.call_count).is_equal_to(1)
    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


def test_auth_error_on_fallback_propagates() -> None:
    """Propagate authentication error raised by a fallback model."""
    provider = _make_provider()
    provider.complete.side_effect = [
        AIProviderError("primary down"),
        AIAuthenticationError("bad key on fallback"),
    ]

    with pytest.raises(AIAuthenticationError, match="bad key on fallback"):
        complete_with_fallback(
            provider,
            "hello",
            fallback_models=["fb-1"],
        )

    assert_that(provider.complete.call_count).is_equal_to(2)
    assert_that(provider.model_name).is_equal_to("primary-model")  # restored


# -- TestCompleteWithFallbackModelRestoration: model_name restored.


def test_model_restored_on_auth_error() -> None:
    """Restore original model after authentication error."""
    provider = _make_provider("orig")
    provider.complete.side_effect = AIAuthenticationError("err")

    with pytest.raises(AIAuthenticationError):
        complete_with_fallback(
            provider,
            "hello",
            fallback_models=["x"],
        )

    assert_that(provider.model_name).is_equal_to("orig")


def test_model_restored_on_provider_error() -> None:
    """Restore original model after provider error."""
    provider = _make_provider("orig")
    provider.complete.side_effect = AIProviderError("err")

    with pytest.raises(AIProviderError):
        complete_with_fallback(provider, "hello")

    assert_that(provider.model_name).is_equal_to("orig")


def test_model_restored_on_success() -> None:
    """Restore original model after successful fallback."""
    provider = _make_provider("orig")
    provider.complete.side_effect = [
        AIProviderError("fail"),
        _ok_response("fb"),
    ]

    complete_with_fallback(provider, "hello", fallback_models=["fb"])

    assert_that(provider.model_name).is_equal_to("orig")


# -- TestCompleteWithFallbackKwargsPassthrough: kwargs forwarded. -


def test_forwards_all_kwargs() -> None:
    """Forward all keyword arguments to provider.complete."""
    provider = _make_provider()
    provider.complete.return_value = _ok_response()

    complete_with_fallback(
        provider,
        "hello",
        system="sys",
        max_tokens=512,
        timeout=30.0,
    )

    provider.complete.assert_called_once_with(
        "hello",
        system="sys",
        max_tokens=512,
        timeout=30.0,
        repo_root=None,
        use_one_shot=False,
        model="primary-model",
        cli_schema=None,
    )


# -- TestCompleteWithFallbackConcurrency: parallel calls must not serialize.


def test_with_fallback_parallel_calls_overlap() -> None:
    """Prove _with_fallback does not serialize concurrent provider calls."""
    provider = _make_provider()
    worker_count = 5
    sleep_seconds = 0.05
    start_barrier = threading.Barrier(worker_count)
    active_lock = threading.Lock()
    active_calls = 0
    max_concurrent_calls = 0

    def slow_attempt(
        _prompt: str,
        _system: str | None,
        _max_tokens: int,
        _timeout: float,
        model: str,
    ) -> str:
        """Sleep to simulate provider latency and track concurrent overlap."""
        nonlocal active_calls, max_concurrent_calls
        start_barrier.wait()
        with active_lock:
            active_calls += 1
            max_concurrent_calls = max(max_concurrent_calls, active_calls)
        time.sleep(sleep_seconds)
        with active_lock:
            active_calls -= 1
        return model

    def run_fallback(_: int) -> str:
        """Invoke _with_fallback from a worker thread."""
        return _with_fallback(provider, slow_attempt, "hello")

    started_at = time.perf_counter()
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(pool.map(run_fallback, range(worker_count)))
    elapsed = time.perf_counter() - started_at

    serialized_minimum = sleep_seconds * worker_count
    assert_that(max_concurrent_calls).is_greater_than_or_equal_to(2)
    assert_that(elapsed).is_less_than(serialized_minimum * 0.75)
    assert_that(results).is_length(worker_count)
    assert_that(set(results)).is_equal_to({"primary-model"})


def test_complete_with_fallback_parallel_calls_overlap() -> None:
    """Prove complete_with_fallback allows concurrent provider.complete calls."""
    provider = _make_provider()
    worker_count = 5
    sleep_seconds = 0.05
    start_barrier = threading.Barrier(worker_count)

    def slow_complete(*_args: object, **_kwargs: object) -> AIResponse:
        """Sleep to simulate provider HTTP latency."""
        start_barrier.wait()
        time.sleep(sleep_seconds)
        return _ok_response()

    provider.complete.side_effect = slow_complete

    started_at = time.perf_counter()
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(
            pool.map(
                lambda _: complete_with_fallback(provider, "hello"),
                range(worker_count),
            ),
        )
    elapsed = time.perf_counter() - started_at

    serialized_minimum = sleep_seconds * worker_count
    assert_that(elapsed).is_less_than(serialized_minimum * 0.75)
    assert_that(results).is_length(worker_count)
    for result in results:
        assert_that(result.content).is_equal_to("ok")
    assert_that(provider.complete.call_count).is_equal_to(worker_count)
