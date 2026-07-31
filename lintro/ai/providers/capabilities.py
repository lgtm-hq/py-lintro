"""Provider capability declarations.

Every provider declares what it supports for the transport it was constructed
with, so orchestration code can branch on a capability instead of on provider
identity (``provider.name == AIProvider.CURSOR``). Adding a provider then means
declaring capabilities rather than auditing scattered conditionals (#1241).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ProviderCapabilities"]


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What a provider supports on its configured transport.

    Attributes:
        supports_sessions: The transport can resume a previous turn, so a
            multi-turn flow may reuse one session instead of re-sending the
            full context per call.
        supports_structured_output: The transport can be handed a JSON schema
            and return output conforming to it natively.
        supports_streaming: The transport can yield tokens incrementally rather
            than only a complete response.
    """

    supports_sessions: bool = False
    supports_structured_output: bool = False
    supports_streaming: bool = False
