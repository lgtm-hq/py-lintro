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

import re
from abc import abstractmethod
from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport
from lintro.ai.providers.base import BaseAIProvider
from lintro.ai.providers.constants import (
    DEFAULT_PER_CALL_MAX_TOKENS,
    DEFAULT_TIMEOUT,
)
from lintro.ai.providers.reported_model import ReportedModel
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
    served_model: str | None = None,
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
        served_model: The model the vendor's response names, when it names
            one (#2867). ``None`` records no model evidence.

    Returns:
        AIResponse: The completed response with usage metadata.
    """
    served = (served_model or "").strip()
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
        # Evidence is what the vendor's response says it served, never the
        # request echoed back; a response naming none stays unreported.
        reported_models=(
            (served_model_evidence(requested=model, served=served),) if served else ()
        ),
    )


#: A vendor's dated snapshot id for an undated alias: ``gpt-5-2026-01-01``
#: or ``claude-sonnet-4-20250514`` for ``gpt-5`` / ``claude-sonnet-4``.
_DATED_SNAPSHOT = re.compile(r"^(?P<base>.+?)-(?:\d{4}-\d{2}-\d{2}|\d{8})$")


def served_model_evidence(*, requested: str, served: str) -> ReportedModel:
    """Describe the model a vendor's response names, keyed by what was asked.

    An undated alias is answered by its dated snapshot (OpenAI and Anthropic
    both do this). The snapshot is the same model, so the evidence keeps the
    served id and names the alias as its canonical form; otherwise the
    served id is both (#2867). A ``-latest`` alias is not folded: its served
    id does not start with the alias, so it fails closed as a mismatch
    unless the lane allows the dated id.

    Args:
        requested: The model the call asked for.
        served: The model id the vendor's response carries.

    Returns:
        The reported model, with ``provider`` set to ``api``.
    """
    match = _DATED_SNAPSHOT.match(served)
    canonical = (
        requested if match is not None and match.group("base") == requested else served
    )
    return ReportedModel(model=served, canonical=canonical, provider="api")


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
            return await super().stream_complete(
                prompt,
                system=system,
                max_tokens=max_tokens,
                timeout=timeout,
                model=model,
            )
        return await self._stream_api(
            prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
            model=model,
        )
