"""Which credential a review run actually forwarded to its provider (#2867)."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["CredentialKind"]


class CredentialKind(StrEnum):
    """The kind of credential present in the environment lintro built.

    Recorded on every run so a sticky can name what authenticated the
    request rather than what the ``--bare`` policy implied (ADR-0011,
    decision B). The values are the binding names the review-lanes manifest
    uses, so a lane row and a run record speak the same vocabulary.

    Attributes:
        CLAUDE_OAUTH: The ``claude`` CLI's OAuth session
            (``CLAUDE_CODE_OAUTH_TOKEN``), billed to a subscription.
        API_KEY: A metered vendor API key (``ANTHROPIC_API_KEY`` or an
            ``apiKeyHelper``, ``OPENAI_API_KEY``), or the API transport.
        ZAI_TOKEN: A gateway token forwarded as ``ANTHROPIC_AUTH_TOKEN`` to
            an Anthropic-protocol endpoint that is not Anthropic's.
        CODEX_OAUTH: The ``codex`` CLI's ChatGPT-plan session file.
        CURSOR_API_KEY: ``CURSOR_API_KEY`` for the ``agent`` CLI.
        UNKNOWN: No recognised credential was present.
    """

    CLAUDE_OAUTH = "claude_oauth"
    API_KEY = "api_key"
    ZAI_TOKEN = "zai_token"  # nosec B105 - a binding name, not a secret
    CODEX_OAUTH = "codex_oauth"
    CURSOR_API_KEY = "cursor_api_key"
    UNKNOWN = "unknown"
