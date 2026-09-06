"""Tests for the AI streaming console display."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator

from assertpy import assert_that
from rich.console import Console

from lintro.ai.display.streaming import async_stream_to_console, stream_to_console
from lintro.ai.providers.async_stream_result import AsyncAIStreamResult
from lintro.ai.providers.response import AIResponse
from lintro.ai.providers.stream_result import AIStreamResult

#: ANSI escape Rich emits for the ``cyan`` style, and the reset that closes it.
CYAN = "\x1b[36m"
RESET = "\x1b[0m"


def _console(buffer: io.StringIO) -> Console:
    """Build a real Rich console that renders styled text into a buffer.

    Forcing terminal mode keeps Rich's ANSI styling on, so a style really
    applied to a chunk shows up in the rendered text rather than only in a
    recorded call.

    Args:
        buffer: Destination the console writes its rendered output to.

    Returns:
        A Rich console writing ANSI-styled text to ``buffer``.
    """
    return Console(
        file=buffer,
        force_terminal=True,
        no_color=False,
        color_system="truecolor",
        width=200,
    )


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
    buffer = io.StringIO()

    result = stream_to_console(_stream([]), _console(buffer))

    assert_that(result).is_equal_to("")
    assert_that(buffer.getvalue()).is_equal_to("\n")


def test_renders_single_chunk() -> None:
    """A single chunk is printed and returned verbatim."""
    buffer = io.StringIO()

    result = stream_to_console(_stream(["hello"]), _console(buffer))

    assert_that(result).is_equal_to("hello")
    assert_that(buffer.getvalue()).is_equal_to("hello\n")


def test_markup_and_highlighting_never_rewrite_a_chunk() -> None:
    """Rich markup and syntax highlighting stay off for streamed text."""
    buffer = io.StringIO()

    result = stream_to_console(
        _stream(["[bold]not markup[/bold] 42"]),
        _console(buffer),
    )

    assert_that(result).is_equal_to("[bold]not markup[/bold] 42")
    assert_that(buffer.getvalue()).is_equal_to("[bold]not markup[/bold] 42\n")


def test_concatenates_multiple_chunks() -> None:
    """Multiple chunks are streamed in order and joined into the return value."""
    buffer = io.StringIO()

    result = stream_to_console(_stream(["a", "b", "c"]), _console(buffer))

    assert_that(result).is_equal_to("abc")
    # ``end=""`` on every chunk means the three arrive unseparated, followed by
    # the single trailing newline.
    assert_that(buffer.getvalue()).is_equal_to("abc\n")


def test_passes_style_to_console() -> None:
    """A non-empty style string is forwarded to each chunk print."""
    buffer = io.StringIO()

    stream_to_console(_stream(["x"]), _console(buffer), style="cyan")

    assert_that(buffer.getvalue()).is_equal_to(f"{CYAN}x{RESET}\n")


def test_empty_style_becomes_none() -> None:
    """An empty style string is normalised to None for the console."""
    buffer = io.StringIO()

    stream_to_console(_stream(["x"]), _console(buffer))

    assert_that(buffer.getvalue()).is_equal_to("x\n")
    assert_that(buffer.getvalue()).does_not_contain(CYAN)


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
    buffer = io.StringIO()

    result = await async_stream_to_console(_async_stream([]), _console(buffer))

    assert_that(result).is_equal_to("")
    assert_that(buffer.getvalue()).is_equal_to("\n")


async def test_async_streams_chunks_in_order() -> None:
    """Chunks reach the console in arrival order and are concatenated."""
    buffer = io.StringIO()

    result = await async_stream_to_console(
        _async_stream(["a", "b", "c"]),
        _console(buffer),
    )

    assert_that(result).is_equal_to("abc")
    assert_that(buffer.getvalue()).is_equal_to("abc\n")


async def test_async_applies_style_to_each_chunk() -> None:
    """The configured Rich style is applied to every streamed chunk."""
    buffer = io.StringIO()

    await async_stream_to_console(
        _async_stream(["a", "b"]),
        _console(buffer),
        style="cyan",
    )

    assert_that(buffer.getvalue()).is_equal_to(f"{CYAN}a{RESET}{CYAN}b{RESET}\n")
