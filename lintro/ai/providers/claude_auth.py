"""Auth-mode detection for the Anthropic ``claude`` CLI transport.

``claude --bare`` disables OAuth session login: in bare mode the binary
authenticates *only* against an API key (``ANTHROPIC_API_KEY`` or an
``apiKeyHelper`` declared in Claude Code settings). Sending it unconditionally
therefore made ``lintro review --transport cli`` unusable for every
subscription-authenticated user, who has a perfectly working ``claude`` login
and no API key at all (#1838).

This module decides, per call, whether ``--bare`` is safe to send:

* ``CliBareMode.AUTO`` (default) -- send it only when an API key is reachable
  by the CLI, so a bare invocation can actually authenticate.
* ``CliBareMode.ALWAYS`` / ``CliBareMode.NEVER`` -- explicit escape hatches, so
  neither detection false-negative nor false-positive requires a ``PATH`` shim
  to work around.

The override is readable from config (``ai.cli_bare``) and from the
``LINTRO_CLI_BARE`` environment variable, which wins so CI can force a mode
without editing a checked-in config file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger

from lintro.ai.enums import CliBareMode

__all__ = [
    "BARE_MODE_ENV",
    "CLAUDE_API_KEY_ENV",
    "claude_api_key_available",
    "resolve_bare_mode",
    "should_send_bare",
]

#: Environment variable that overrides the configured ``ai.cli_bare`` policy.
BARE_MODE_ENV = "LINTRO_CLI_BARE"

#: The only API-key variable the ``claude`` binary itself reads. A custom
#: ``ai.api_key_env`` renames the variable lintro's *API* transport reads and is
#: deliberately not consulted here: the CLI subprocess would never look at it,
#: so treating it as proof of an API key would send ``--bare`` into a CLI that
#: cannot authenticate.
CLAUDE_API_KEY_ENV = "ANTHROPIC_API_KEY"

#: Truthy/falsey spellings accepted by ``LINTRO_CLI_BARE`` in addition to the
#: :class:`CliBareMode` values themselves.
_BOOLEAN_ALIASES: dict[str, CliBareMode] = {
    "1": CliBareMode.ALWAYS,
    "true": CliBareMode.ALWAYS,
    "yes": CliBareMode.ALWAYS,
    "on": CliBareMode.ALWAYS,
    "0": CliBareMode.NEVER,
    "false": CliBareMode.NEVER,
    "no": CliBareMode.NEVER,
    "off": CliBareMode.NEVER,
}


def _settings_candidates(*, cwd: str | None) -> tuple[Path, ...]:
    """Return the Claude Code settings files that may declare ``apiKeyHelper``.

    Mirrors Claude Code's own settings hierarchy closely enough for a
    presence check: the user-level file (relocatable via
    ``CLAUDE_CONFIG_DIR``) plus the project-level files under the working
    directory the CLI is spawned in.

    Args:
        cwd: Working directory the CLI subprocess runs in, or ``None`` for the
            current process directory.

    Returns:
        Candidate settings paths, in no particular precedence order — any one
        of them declaring a helper is enough.
    """
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    user_dir = Path(config_dir) if config_dir else Path.home() / ".claude"
    project_dir = Path(cwd) if cwd else Path.cwd()
    return (
        user_dir / "settings.json",
        project_dir / ".claude" / "settings.json",
        project_dir / ".claude" / "settings.local.json",
    )


def _declares_api_key_helper(path: Path) -> bool:
    """Return whether *path* is a settings file declaring ``apiKeyHelper``.

    Args:
        path: Candidate settings file.

    Returns:
        True when the file parses as a JSON object carrying a non-empty
        ``apiKeyHelper`` string. Unreadable or malformed files are treated as
        "no helper" rather than as an error: settings files are outside
        lintro's control and a broken one must not fail a review.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug(f"Ignoring unparseable Claude settings file: {path}")
        return False
    if not isinstance(data, dict):
        return False
    helper = data.get("apiKeyHelper")
    return isinstance(helper, str) and bool(helper.strip())


def claude_api_key_available(*, cwd: str | None = None) -> bool:
    """Return whether the ``claude`` CLI can authenticate from an API key.

    Args:
        cwd: Working directory the CLI subprocess runs in, used to locate
            project-level settings. Defaults to the current directory.

    Returns:
        True when ``ANTHROPIC_API_KEY`` is set to a non-empty value, or when a
        reachable Claude Code settings file declares an ``apiKeyHelper``.
    """
    if os.environ.get(CLAUDE_API_KEY_ENV, "").strip():
        return True
    return any(
        _declares_api_key_helper(candidate)
        for candidate in _settings_candidates(cwd=cwd)
    )


def resolve_bare_mode(configured: CliBareMode = CliBareMode.AUTO) -> CliBareMode:
    """Return the effective bare-mode policy, honouring the env override.

    Args:
        configured: The mode set in ``ai.cli_bare``.

    Returns:
        The mode named by ``LINTRO_CLI_BARE`` when it is set to a recognised
        value, otherwise *configured*. An unrecognised value is warned about and
        ignored, because silently guessing a mode is exactly the failure this
        issue was about.
    """
    raw = os.environ.get(BARE_MODE_ENV)
    if raw is None:
        return configured
    normalized = raw.strip().lower()
    if not normalized:
        return configured
    alias = _BOOLEAN_ALIASES.get(normalized)
    if alias is not None:
        return alias
    try:
        return CliBareMode(normalized)
    except ValueError:
        logger.warning(
            f"Ignoring {BARE_MODE_ENV}={raw!r}: expected one of "
            f"{', '.join(mode.value for mode in CliBareMode)}.",
        )
        return configured


def should_send_bare(
    *,
    configured: CliBareMode = CliBareMode.AUTO,
    cwd: str | None = None,
) -> bool:
    """Decide whether this ``claude`` invocation may carry ``--bare``.

    Args:
        configured: The mode set in ``ai.cli_bare``.
        cwd: Working directory the CLI subprocess runs in.

    Returns:
        True when ``--bare`` should be appended to the command line.
    """
    mode = resolve_bare_mode(configured)
    if mode is CliBareMode.ALWAYS:
        return True
    if mode is CliBareMode.NEVER:
        return False
    return claude_api_key_available(cwd=cwd)
