"""Bare-mode policy for the Claude CLI transport."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["CliBareMode"]


class CliBareMode(StrEnum):
    """Whether lintro passes ``--bare`` to the ``claude`` CLI.

    ``claude --bare`` runs the binary without its agentic tool surface, but it
    also disables OAuth session login: in bare mode the CLI authenticates only
    against an API key. Forcing it therefore locks subscription-authenticated
    users out of ``--transport cli`` entirely (see #1838).

    Attributes:
        AUTO: Send ``--bare`` only when an API key is reachable by the CLI
            (default).
        ALWAYS: Always send ``--bare``, even without a detected API key.
        NEVER: Never send ``--bare``; always rely on the CLI's own auth.
    """

    AUTO = auto()
    ALWAYS = auto()
    NEVER = auto()
