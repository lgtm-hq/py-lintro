"""Shared API-transport helpers for the SDK-backed providers.

Anthropic and OpenAI assemble different requests but finish an API completion
identically: log the response transcript event, then build the
:class:`~lintro.ai.providers.response.AIResponse` from the same six values.
They also share the transport dispatch at the top of ``stream_complete``: fall
back to the base chunker under CLI transport, otherwise stream from the SDK.
Both were duplicated windows the #2293-style duplicate check found between the
two packages (issue #2307, AC 3), so they live here rather than in either.

Kept deliberately narrow: the request assembly, streaming accumulation and
usage extraction differ per vendor and stay with the provider that owns them.
This module imports no vendor SDK.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport
from lintro.ai.providers.base import BaseAIProvider
from lintro.ai.providers.constants import (
    DEFAULT_PER_CALL_MAX_TOKENS,
    DEFAULT_TIMEOUT,
)
from lintro.ai.providers.response import AIResponse
from lintro.ai.transcript import TranscriptDirection, log_transcript_event

if TYPE_CHECKING:
    from lintro.ai.provider_enum import AIProvider
    from lintro.ai.providers.async_stream_result import AsyncAIStreamResult

__all__ = ["ApiStreamingProvider", "finish_api_completion"]


def finish_api_completion(
    *,
    provider: AIProvider,
    model: str,
    content: str,
    input_tokens: int,
    output_tokens: int,
    cost: float,
) -> AIResponse:
    """Record the API response transcript event and build the response.

    The transcript payload and the response fields are byte-for-byte what the
    two providers emitted before this helper existed; changing either is a
    behaviour change, not a refactor.

    Args:
        provider: The provider that served the completion.
        model: Effective model identifier used for the call.
        content: The completion text.
        input_tokens: Prompt tokens reported by the vendor.
        output_tokens: Completion tokens reported by the vendor.
        cost: Estimated cost in USD.

    Returns:
        AIResponse: The completed response with usage metadata.
    """
    log_transcript_event(
        provider=provider.value,
        transport=AITransport.API.value,
        direction=TranscriptDirection.RESPONSE,
        payload={
            "model": model,
            "content": content,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_estimate": cost,
        },
    )
    return AIResponse(
        content=content,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=cost,
        provider=provider,
    )


class ApiStreamingProvider(BaseAIProvider):
    """A provider whose API transport streams natively.

    Holds the transport dispatch both SDK-backed providers used to spell out
    identically: CLI transport has no token stream, so it falls back to the
    base class's chunk-the-finished-completion behaviour, and API transport
    goes to the vendor stream in :meth:`_stream_api`. Subclasses implement only
    the vendor-specific half.
    """

    @abstractmethod
    async def _stream_api(
        self,
        prompt: str,
        *,
        system: str | None,
        max_tokens: int,
        timeout: float,
        model: str | None,
    ) -> AsyncAIStreamResult:
        """Stream a completion from the vendor API token-by-token.

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.
            model: Optional per-call model override.

        Returns:
            An AsyncAIStreamResult wrapping the token stream.
        """

    async def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        model: str | None = None,
    ) -> AsyncAIStreamResult:
        """Stream a completion, or fall back to the base chunker under CLI.

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.
            model: Optional per-call model override.

        Returns:
            An AsyncAIStreamResult wrapping the token stream.
        """
        if self._transport == AITransport.CLI:
            # ``model`` is deliberately not forwarded: neither provider passed
            # it on this branch before the migration, and #2307 is
            # behaviour-preserving. Threading the per-call override through the
            # CLI fallback is a real fix, but it belongs to whichever issue
            # owns CLI streaming, not to this move.
            return await super().stream_complete(
                prompt,
                system=system,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        return await self._stream_api(
            prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
            model=model,
        )
