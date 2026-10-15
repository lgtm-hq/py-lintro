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
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

__all__ = [
    "CODEX_API_KEY_ENV",
    "CODEX_HOME_ENV",
    "codex_auth_file",
    "session_embeds_api_key",
    "stored_session",
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


def codex_auth_file(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the path ``codex`` reads its stored session from.

    Args:
        env: Environment to read ``CODEX_HOME`` from; the process environment
            when ``None``.
        home: Home directory the default location is resolved against; the
            process's home when ``None``.

    Returns:
        ``$CODEX_HOME/auth.json`` when the variable is set and non-empty,
        otherwise ``<home>/.codex/auth.json``. An empty ``CODEX_HOME`` falls
        back rather than resolving against the process's working directory,
        which is what a bare ``Path("")`` would do.
    """
    environment = os.environ if env is None else env
    codex_home = environment.get(CODEX_HOME_ENV)
    if codex_home:
        return Path(codex_home) / _CODEX_AUTH_FILENAME
    return (Path.home() if home is None else home) / ".codex" / _CODEX_AUTH_FILENAME


def stored_session(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> dict[str, Any] | None:
    """Return the stored session object, or ``None`` when codex has none to read.

    Args:
        env: Environment to read ``CODEX_HOME`` from; the process environment
            when ``None``.
        home: Home directory the default location is resolved against.

    Returns:
        The parsed ``auth.json`` when it is a JSON object; ``None`` when the
        file is absent, unreadable, not JSON, hostile, or not an object.
    """
    try:
        payload = json.loads(
            codex_auth_file(env=env, home=home).read_text(encoding="utf-8"),
        )
    except (OSError, ValueError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def session_embeds_api_key(session: Mapping[str, Any]) -> bool:
    """Return whether a stored session is an API-key login.

    Args:
        session: The parsed ``auth.json`` object.

    Returns:
        True when the session carries an embedded ``OPENAI_API_KEY``.
    """
    return bool(session.get(_EMBEDDED_API_KEY_FIELD))


def uses_subscription_session(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> bool:
    """Report whether codex will authenticate with a ChatGPT-plan session.

    Permissive about the *contents* of the object, strict about it being one.
    The question being answered is which default model is safe to ask for, and
    the fallback is the API-catalogue default that has always been sent, so a
    session whose fields were renamed should keep counting as a subscription
    rather than silently reintroduce the unsupported-model failure. A file that
    is not a JSON object at all is a different matter: codex writes an object,
    so a list, string or number is not a session it could read, and treating it
    as one would be a guess with no evidence behind it.

    Args:
        env: Environment to read ``CODEX_API_KEY`` and ``CODEX_HOME`` from;
            the process environment when ``None``.
        home: Home directory the default session location is resolved against.

    Returns:
        True when the stored session is a JSON object carrying no embedded API
        key, and no API-key variable is set in the environment. False when the
        file is absent, unreadable, not JSON, not a JSON object, holds an
        embedded key, or ``CODEX_API_KEY`` is set.
    """
    environment = os.environ if env is None else env
    if environment.get(CODEX_API_KEY_ENV):
        return False
    session = stored_session(env=env, home=home)
    return session is not None and not session_embeds_api_key(session)
