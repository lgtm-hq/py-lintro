"""Tests for AIStreamResult and BaseAIProvider.stream_complete()."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.providers.base import AIResponse, AIStreamResult, BaseAIProvider
from lintro.ai.providers.constants import DEFAULT_PER_CALL_MAX_TOKENS, DEFAULT_TIMEOUT


class _StubProvider(BaseAIProvider):
    """Minimal concrete provider for testing the base default behaviour."""

    def __init__(self, response: AIResponse) -> None:
        self._response = response
        self._provider_name = "stub"
        self._has_sdk = True
        self._model = "stub-model"
        self._api_key_env = "STUB_KEY"
        self._max_tokens = DEFAULT_PER_CALL_MAX_TOKENS
        self._base_url = None
        self._client = "fake"

    def _create_client(self, *, api_key: str) -> object:
        return "fake"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIResponse:
        return self._response


def _make_response(content: str = "hello world") -> AIResponse:
    return AIResponse(
        content=content,
        model="test-model",
        input_tokens=10,
        output_tokens=5,
        cost_estimate=0.001,
        provider="test",
    )


def test_stream_result_iter_yields_chunks() -> None:
    """Iterating an AIStreamResult yields all provided chunks."""
    chunks = ["foo", "bar", "baz"]
    resp = _make_response("foobarbaz")
    result = AIStreamResult(_chunks=iter(chunks), _on_done=lambda: resp)

    assert_that(list(result)).is_equal_to(["foo", "bar", "baz"])


def test_stream_result_response_returns_metadata() -> None:
    """response() returns the AIResponse supplied by _on_done."""
    resp = _make_response()
    result = AIStreamResult(_chunks=iter([]), _on_done=lambda: resp)
    list(result)

    assert_that(result.response()).is_equal_to(resp)


def test_stream_result_collect_concatenates_and_returns_response() -> None:
    """collect() joins chunks and populates content in the returned AIResponse."""
    resp = _make_response("")
    result = AIStreamResult(
        _chunks=iter(["alpha", " ", "beta"]),
        _on_done=lambda: resp,
    )

    collected = result.collect()

    assert_that(collected.content).is_equal_to("alpha beta")
    assert_that(collected.model).is_equal_to("test-model")
    assert_that(collected.input_tokens).is_equal_to(10)
    assert_that(collected.output_tokens).is_equal_to(5)
    assert_that(collected.provider).is_equal_to("test")


def test_stream_result_collect_empty_stream() -> None:
    """collect() with no chunks returns empty content."""
    resp = _make_response("")
    result = AIStreamResult(_chunks=iter([]), _on_done=lambda: resp)

    assert_that(result.collect().content).is_equal_to("")


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        (["a"], "a"),
        (["a", "b", "c"], "abc"),
        ([""], ""),
        (["hello ", "world"], "hello world"),
    ],
    ids=["single", "multi", "empty-chunk", "with-space"],
)
def test_stream_result_collect_various_chunk_patterns(
    chunks: list[str],
    expected: str,
) -> None:
    """collect() works correctly with various chunk patterns."""
    resp = _make_response("")
    result = AIStreamResult(_chunks=iter(chunks), _on_done=lambda: resp)

    assert_that(result.collect().content).is_equal_to(expected)


def test_base_provider_stream_complete_delegates_to_complete() -> None:
    """Default stream_complete wraps complete() in a single-chunk stream."""
    resp = _make_response("delegated content")
    provider = _StubProvider(response=resp)

    stream = provider.stream_complete("test prompt")
    collected = stream.collect()

    assert_that(collected.content).is_equal_to("delegated content")
    assert_that(collected.model).is_equal_to("test-model")
    assert_that(collected.provider).is_equal_to("test")


def test_base_provider_stream_complete_passes_kwargs() -> None:
    """Default stream_complete forwards system/max_tokens/timeout to complete."""
    calls: list[dict[str, object]] = []

    class _CapturingProvider(_StubProvider):
        def complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
            timeout: float = DEFAULT_TIMEOUT,
        ) -> AIResponse:
            calls.append(
                {
                    "prompt": prompt,
                    "system": system,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                },
            )
            return _make_response()

    provider = _CapturingProvider(response=_make_response())
    provider.stream_complete(
        "my prompt",
        system="sys",
        max_tokens=512,
        timeout=30,
    )

    assert_that(calls).is_length(1)
    assert_that(calls[0]["prompt"]).is_equal_to("my prompt")
    assert_that(calls[0]["system"]).is_equal_to("sys")
    assert_that(calls[0]["max_tokens"]).is_equal_to(512)
    assert_that(calls[0]["timeout"]).is_equal_to(30)


def test_base_provider_stream_complete_single_chunk_iteration() -> None:
    """Default stream_complete yields exactly one chunk with the full content."""
    resp = _make_response("one shot")
    provider = _StubProvider(response=resp)

    stream = provider.stream_complete("p")
    chunks = list(stream)

    assert_that(chunks).is_equal_to(["one shot"])
