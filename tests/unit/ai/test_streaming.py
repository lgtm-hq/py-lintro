"""Tests for the AI streaming console display."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.ai.display.streaming import async_stream_to_console, stream_to_console
from lintro.ai.providers.async_stream_result import AsyncAIStreamResult
from lintro.ai.providers.response import AIResponse
from lintro.ai.providers.stream_result import AIStreamResult


def _stream(chunks: list[str]) -> AIStreamResult:
    """Build an AIStreamResult over a fixed list of chunks.

    Args:
        chunks: Text chunks the stream yields.

    Returns:
        A stream result over the chunks.
    """

    def _on_done() -> AIResponse:
        return AIResponse(
            content="".join(chunks),
            model="test",
            input_tokens=0,
            output_tokens=0,
            cost_estimate=0.0,
            provider="test",
        )

    return AIStreamResult(_chunks=iter(chunks), _on_done=_on_done)


def test_returns_empty_string_for_empty_stream() -> None:
    """An exhausted-empty stream renders nothing but a trailing newline."""
    console = MagicMock()

    result = stream_to_console(_stream([]), console)

    assert_that(result).is_equal_to("")
    # Only the final newline print() call, no chunk prints.
    assert_that(console.print.call_count).is_equal_to(1)
    console.print.assert_called_once_with()


def test_renders_single_chunk() -> None:
    """A single chunk is printed and returned verbatim."""
    console = MagicMock()

    result = stream_to_console(_stream(["hello"]), console)

    assert_that(result).is_equal_to("hello")
    first_call = console.print.call_args_list[0]
    assert_that(first_call.args).is_equal_to(("hello",))
    assert_that(first_call.kwargs["end"]).is_equal_to("")
    assert_that(first_call.kwargs["highlight"]).is_false()
    assert_that(first_call.kwargs["markup"]).is_false()


def test_concatenates_multiple_chunks() -> None:
    """Multiple chunks are streamed in order and joined into the return value."""
    console = MagicMock()

    result = stream_to_console(_stream(["a", "b", "c"]), console)

    assert_that(result).is_equal_to("abc")
    # Three chunk prints plus one trailing newline print.
    assert_that(console.print.call_count).is_equal_to(4)
    printed = [call.args[0] for call in console.print.call_args_list[:3]]
    assert_that(printed).is_equal_to(["a", "b", "c"])


def test_passes_style_to_console() -> None:
    """A non-empty style string is forwarded to each chunk print."""
    console = MagicMock()

    stream_to_console(_stream(["x"]), console, style="cyan")

    first_call = console.print.call_args_list[0]
    assert_that(first_call.kwargs["style"]).is_equal_to("cyan")


def test_empty_style_becomes_none() -> None:
    """An empty style string is normalised to None for the console."""
    console = MagicMock()

    stream_to_console(_stream(["x"]), console)

    first_call = console.print.call_args_list[0]
    assert_that(first_call.kwargs["style"]).is_none()


def _async_stream(chunks: list[str]) -> AsyncAIStreamResult:
    """Build an AsyncAIStreamResult over a fixed list of chunks.

    Args:
        chunks: Text chunks the stream yields.

    Returns:
        An async stream result over the chunks.
    """

    async def _chunks() -> AsyncIterator[str]:
        """Yield each chunk in order.

        Yields:
            str: One chunk at a time.
        """
        for chunk in chunks:
            yield chunk

    def _on_done() -> AIResponse:
        """Return the finalized response.

        Returns:
            A response carrying the joined chunks.
        """
        return AIResponse(
            content="".join(chunks),
            model="test",
            input_tokens=0,
            output_tokens=0,
            cost_estimate=0.0,
            provider="test",
        )

    return AsyncAIStreamResult(_chunks=_chunks(), _on_done=_on_done)


async def test_async_returns_empty_string_for_empty_stream() -> None:
    """An empty async stream renders nothing but a trailing newline."""
    console = MagicMock()

    result = await async_stream_to_console(_async_stream([]), console)

    assert_that(result).is_equal_to("")
    console.print.assert_called_once_with()


async def test_async_streams_chunks_in_order() -> None:
    """Chunks reach the console in arrival order and are concatenated."""
    console = MagicMock()

    result = await async_stream_to_console(_async_stream(["a", "b", "c"]), console)

    assert_that(result).is_equal_to("abc")
    printed = [call.args[0] for call in console.print.call_args_list if call.args]
    assert_that(printed).is_equal_to(["a", "b", "c"])


async def test_async_applies_style_to_each_chunk() -> None:
    """The configured Rich style is applied to every streamed chunk."""
    console = MagicMock()

    await async_stream_to_console(_async_stream(["x"]), console, style="cyan")

    first_call = console.print.call_args_list[0]
    assert_that(first_call.kwargs["style"]).is_equal_to("cyan")
