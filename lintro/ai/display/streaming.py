"""Streaming display utilities for AI output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.providers.base import AIStreamResult

if TYPE_CHECKING:
    from rich.console import Console


def stream_to_console(
    stream_result: AIStreamResult,
    console: Console,
    *,
    style: str = "",
) -> str:
    """Stream AI tokens to a Rich console as they arrive.

    Args:
        stream_result: The streaming result to display.
        console: Rich Console instance for output.
        style: Optional Rich style string applied to each chunk.

    Returns:
        The full concatenated text that was streamed.
    """
    parts: list[str] = []
    for chunk in stream_result:
        console.print(chunk, end="", style=style or None, highlight=False, markup=False)
        parts.append(chunk)
    console.print()
    return "".join(parts)


__all__ = ["stream_to_console"]
