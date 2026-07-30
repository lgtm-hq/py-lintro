"""Async AI provider streaming result wrapper.

Contains the ``AsyncAIStreamResult`` dataclass that wraps an async token
iterator and provides finalized metadata after the stream is exhausted.
It is the async counterpart of
:class:`~lintro.ai.providers.stream_result.AIStreamResult`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from lintro.ai.providers.response import AIResponse


@dataclass
class AsyncAIStreamResult:
    """Wraps an async token iterator and exposes finalized metadata.

    ``_chunks`` yields text chunks as they arrive; ``_on_done`` returns the
    finalized response and is only valid once the iterator is exhausted.
    """

    _chunks: AsyncIterator[str]
    _on_done: Callable[[], AIResponse]
    _consumed: bool = field(default=False, init=False)

    async def __aiter__(self) -> AsyncIterator[str]:
        """Yield text chunks from the underlying async iterator.

        The stream is marked consumed as soon as iteration starts, so a
        second consumer (a repeat ``async for``, or ``collect()`` after a
        partial read) cannot silently receive a truncated stream.

        Yields:
            str: Text chunks in arrival order.
        """
        self._consumed = True
        async for chunk in self._chunks:
            yield chunk

    def response(self) -> AIResponse:
        """Return the finalized AIResponse.

        Only valid after iteration completes.

        Returns:
            The finalized AIResponse with usage metadata.
        """
        return self._on_done()

    async def collect(self) -> AIResponse:
        """Consume all tokens and return the complete AIResponse.

        May only be called once -- a second call raises ``RuntimeError``
        because the underlying iterator has already been exhausted.

        Returns:
            AIResponse with concatenated content and usage metadata.

        Raises:
            RuntimeError: If the stream has already been consumed.
        """
        if self._consumed:
            raise RuntimeError("AsyncAIStreamResult already consumed")
        parts: list[str] = [chunk async for chunk in self]
        resp = self.response()
        return AIResponse(
            content="".join(parts),
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_estimate=resp.cost_estimate,
            provider=resp.provider,
        )
