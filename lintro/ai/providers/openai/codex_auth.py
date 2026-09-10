"""Which credential the ``codex`` CLI will actually use, without spawning it.

The Codex CLI authenticates two ways, and they do not accept the same models
(#2537). A metered API key bills the OpenAI API and reaches the API model
catalogue; a ChatGPT-plan session bills the subscription and reaches only the
models that plan offers — asking it for an API-catalogue model fails the whole
call with ``The 'gpt-4o' model is not supported when using Codex with a ChatGPT
account``. lintro therefore has to know which credential is in play *before* it
decides what to send as ``--model``.

Like :mod:`lintro.ai.providers.cli_auth_probe` this is presence-only and never
spawns the binary. Unlike that probe, which answers a doctor question about the
user's home directory, this one honours ``CODEX_HOME``: CI restores the session
outside ``$HOME`` (a session under a temporary home makes codex refuse to run),
so a home-relative check would misread every CI run as unauthenticated.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final

__all__ = [
    "CODEX_API_KEY_ENV",
    "CODEX_HOME_ENV",
    "codex_auth_file",
    "uses_subscription_session",
]

#: Names the directory holding ``auth.json``; overrides ``$HOME/.codex``.
CODEX_HOME_ENV: Final[str] = "CODEX_HOME"

#: The API key the ``codex`` binary itself reads. Note it is *not*
#: ``OPENAI_API_KEY``: codex never looks at the SDK's variable.
CODEX_API_KEY_ENV: Final[str] = "CODEX_API_KEY"

#: Written by ``codex login`` inside the Codex home.
_CODEX_AUTH_FILENAME: Final[str] = "auth.json"

#: Key under which an API-key login stores the key inside ``auth.json``. A file
#: carrying one is an API-key session even with no environment variable set.
_EMBEDDED_API_KEY_FIELD: Final[str] = "OPENAI_API_KEY"


def codex_auth_file() -> Path:
    """Return the path ``codex`` reads its stored session from.

    Returns:
        ``$CODEX_HOME/auth.json`` when the variable is set and non-empty,
        otherwise ``~/.codex/auth.json``.
    """
    codex_home = os.environ.get(CODEX_HOME_ENV)
    if codex_home:
        return Path(codex_home) / _CODEX_AUTH_FILENAME
    return Path.home() / ".codex" / _CODEX_AUTH_FILENAME


def uses_subscription_session() -> bool:
    """Report whether codex will authenticate with a ChatGPT-plan session.

    Deliberately permissive about the file's shape: the question being answered
    is which *default model* is safe to ask for, and the fallback is the
    API-catalogue default that has always been sent. A stored session whose
    layout changed should keep being treated as a subscription rather than
    silently reintroduce the unsupported-model failure.

    Returns:
        True when a stored session exists, carries no embedded API key, and no
        API-key variable is set in the environment.
    """
    if os.environ.get(CODEX_API_KEY_ENV):
        return False
    try:
        payload = json.loads(codex_auth_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    return not payload.get(_EMBEDDED_API_KEY_FIELD)
