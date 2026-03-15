"""AI provider streaming result wrapper.

Contains the ``AIStreamResult`` dataclass that wraps a token iterator
and provides finalized metadata after the stream is exhausted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from lintro.ai.providers.response import AIResponse


@dataclass
class AIStreamResult:
    """Wraps a token iterator and provides finalized metadata after exhaustion."""

    _chunks: Iterator[str]
    _on_done: Callable[[], AIResponse]
    _consumed: bool = field(default=False, init=False)

    def __iter__(self) -> Iterator[str]:
        """Yield text chunks from the underlying iterator."""
        yield from self._chunks
        self._consumed = True

    def response(self) -> AIResponse:
        """Return the finalized AIResponse.

        Only valid after iteration completes.

        Returns:
            The finalized AIResponse with usage metadata.
        """
        return self._on_done()

    def collect(self) -> AIResponse:
        """Consume all tokens and return the complete AIResponse.

        May only be called once -- a second call raises ``RuntimeError``
        because the underlying iterator has already been exhausted.

        Returns:
            AIResponse with concatenated content and usage metadata.

        Raises:
            RuntimeError: If the stream has already been consumed.
        """
        if self._consumed:
            raise RuntimeError("AIStreamResult already consumed")
        content = "".join(self)
        resp = self.response()
        return AIResponse(
            content=content,
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_estimate=resp.cost_estimate,
            provider=resp.provider,
        )
