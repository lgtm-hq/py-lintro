"""Tests for the sync and async AI stream results and stream_complete()."""

from __future__ import annotations

import subprocess  # nosec B404 - CompletedProcess fixtures only; no process spawn
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.providers.anthropic.provider import AnthropicProvider
from lintro.ai.providers.base import (
    AIResponse,
    AIStreamResult,
    AsyncAIStreamResult,
    BaseAIProvider,
)
from lintro.ai.providers.constants import DEFAULT_PER_CALL_MAX_TOKENS, DEFAULT_TIMEOUT
from lintro.ai.providers.openai.provider import OpenAIProvider
from tests.unit.ai.conftest import patch_cli_exec

#: Claude CLI stdout for a successful one-shot completion.
_CLAUDE_STDOUT = '{"result":"ok","usage":{"input_tokens":1,"output_tokens":1}}'

#: Codex CLI stdout for a successful ``codex exec --json`` run.
_CODEX_STDOUT = (
    '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n'
    '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
)


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

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        repo_root: str | None = None,
        use_one_shot: bool = False,
        model: str | None = None,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        """Return the canned response.

        Args:
            prompt: Ignored user prompt.
            system: Ignored system prompt.
            max_tokens: Ignored token cap.
            timeout: Ignored timeout.
            repo_root: Ignored repository root.
            use_one_shot: Ignored session flag.
            model: Ignored model override.
            cli_schema: Ignored schema request.

        Returns:
            The canned response.
        """
        del model, cli_schema
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


async def test_base_provider_stream_complete_delegates_to_complete() -> None:
    """Default stream_complete wraps complete() in a single-chunk stream."""
    resp = _make_response("delegated content")
    provider = _StubProvider(response=resp)

    stream = await provider.stream_complete("test prompt")
    collected = await stream.collect()

    assert_that(collected.content).is_equal_to("delegated content")
    assert_that(collected.model).is_equal_to("test-model")
    assert_that(collected.provider).is_equal_to("test")


async def test_base_provider_stream_complete_passes_kwargs() -> None:
    """Default stream_complete forwards system/max_tokens/timeout to complete."""
    calls: list[dict[str, object]] = []

    class _CapturingProvider(_StubProvider):
        async def complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
            timeout: float = DEFAULT_TIMEOUT,
            repo_root: str | None = None,
            use_one_shot: bool = False,
            model: str | None = None,
            cli_schema: CliSchemaRequest | None = None,
        ) -> AIResponse:
            """Record the call and return a canned response.

            Args:
                prompt: The user prompt.
                system: Optional system prompt.
                max_tokens: Token cap for the call.
                timeout: Request timeout in seconds.
                repo_root: Ignored repository root.
                use_one_shot: Ignored session flag.
                model: Optional model override.
                cli_schema: Ignored schema request.

            Returns:
                A canned response.
            """
            del cli_schema
            calls.append(
                {
                    "prompt": prompt,
                    "system": system,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                    "model": model,
                },
            )
            return _make_response()

    provider = _CapturingProvider(response=_make_response())
    stream = await provider.stream_complete(
        "my prompt",
        system="sys",
        max_tokens=512,
        timeout=30,
    )
    [chunk async for chunk in stream]  # consume the stream

    assert_that(calls).is_length(1)
    assert_that(calls[0]["prompt"]).is_equal_to("my prompt")
    assert_that(calls[0]["system"]).is_equal_to("sys")
    assert_that(calls[0]["max_tokens"]).is_equal_to(512)
    assert_that(calls[0]["timeout"]).is_equal_to(30)


async def test_base_provider_stream_complete_single_chunk_iteration() -> None:
    """Default stream_complete yields exactly one chunk with the full content."""
    resp = _make_response("one shot")
    provider = _StubProvider(response=resp)

    stream = await provider.stream_complete("p")
    chunks = [chunk async for chunk in stream]

    assert_that(chunks).is_equal_to(["one shot"])


def test_collect_raises_on_double_call() -> None:
    """collect() raises RuntimeError when called a second time."""
    resp = _make_response("")
    result = AIStreamResult(
        _chunks=iter(["alpha", " ", "beta"]),
        _on_done=lambda: resp,
    )

    # First call succeeds
    collected = result.collect()
    assert_that(collected.content).is_equal_to("alpha beta")

    # Second call raises
    with pytest.raises(RuntimeError, match="already consumed"):
        result.collect()


async def _achunks(chunks: list[str]) -> AsyncIterator[str]:
    """Yield chunks from an async generator.

    Args:
        chunks: Text chunks to yield.

    Yields:
        str: Each chunk in order.
    """
    for chunk in chunks:
        yield chunk


async def test_async_stream_result_aiter_yields_chunks() -> None:
    """Async iteration yields all provided chunks."""
    resp = _make_response("foobarbaz")
    result = AsyncAIStreamResult(
        _chunks=_achunks(["foo", "bar", "baz"]),
        _on_done=lambda: resp,
    )

    assert_that([chunk async for chunk in result]).is_equal_to(["foo", "bar", "baz"])


async def test_async_stream_result_collect_concatenates() -> None:
    """collect() joins chunks and carries usage metadata through."""
    resp = _make_response("")
    result = AsyncAIStreamResult(
        _chunks=_achunks(["alpha", " ", "beta"]),
        _on_done=lambda: resp,
    )

    collected = await result.collect()

    assert_that(collected.content).is_equal_to("alpha beta")
    assert_that(collected.model).is_equal_to("test-model")
    assert_that(collected.input_tokens).is_equal_to(10)
    assert_that(collected.output_tokens).is_equal_to(5)
    assert_that(collected.provider).is_equal_to("test")


async def test_async_stream_result_collect_empty_stream() -> None:
    """collect() with no chunks returns empty content."""
    resp = _make_response("")
    result = AsyncAIStreamResult(_chunks=_achunks([]), _on_done=lambda: resp)

    assert_that((await result.collect()).content).is_equal_to("")


async def test_async_stream_result_response_returns_metadata() -> None:
    """response() returns the AIResponse supplied by _on_done."""
    resp = _make_response()
    result = AsyncAIStreamResult(_chunks=_achunks([]), _on_done=lambda: resp)
    [chunk async for chunk in result]

    assert_that(result.response()).is_equal_to(resp)


async def test_async_stream_result_collect_raises_on_double_call() -> None:
    """collect() raises RuntimeError when called a second time."""
    resp = _make_response("")
    result = AsyncAIStreamResult(
        _chunks=_achunks(["alpha", " ", "beta"]),
        _on_done=lambda: resp,
    )

    collected = await result.collect()
    assert_that(collected.content).is_equal_to("alpha beta")

    with pytest.raises(RuntimeError, match="already consumed"):
        await result.collect()


@pytest.mark.parametrize(
    ("finder", "provider_class", "binary", "stdout"),
    [
        pytest.param(
            "lintro.ai.providers.anthropic.provider._find_claude",
            AnthropicProvider,
            "/usr/local/bin/claude",
            _CLAUDE_STDOUT,
            id="anthropic",
        ),
        pytest.param(
            "lintro.ai.providers.openai.provider._find_codex",
            OpenAIProvider,
            "/usr/local/bin/codex",
            _CODEX_STDOUT,
            id="openai",
        ),
    ],
)
async def test_cli_stream_fallback_forwards_the_per_call_model(
    finder: str,
    provider_class: type[AnthropicProvider] | type[OpenAIProvider],
    binary: str,
    stdout: str,
) -> None:
    """Streaming under CLI transport sends the per-call model override (#2449).

    Args:
        finder: Import path of the provider's CLI binary lookup.
        provider_class: Provider class under test.
        binary: Fake path the binary lookup returns.
        stdout: CLI stdout the fake subprocess replays.
    """
    with patch(finder, return_value=binary):
        provider = provider_class(transport=AITransport.CLI)

    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        result = await provider.stream_complete("hi", model="override")
        collected = await result.collect()

    cmd = mock_run.transport_calls[-1].cmd
    assert_that(cmd).contains("--model")
    assert_that(cmd[cmd.index("--model") + 1]).is_equal_to("override")
    assert_that(collected.content).is_equal_to("ok")
