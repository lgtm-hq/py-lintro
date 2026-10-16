#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Bind one review attempt to its resolved lane (ADR-0011 decisions A and D, #2870).

``run-ai-review.sh`` runs this twice per attempt, with the chain and the
lane rows the secret-free ``lane`` job published (``LINTRO_REVIEW_CHAIN``,
``LINTRO_REVIEW_LANES``) and the attempt's index in the chain
(``LINTRO_REVIEW_ATTEMPT``, default ``0``):

``--phase env``
    Print the row-derived exports: the ``LINTRO_REVIEW_*`` facts the run
    record reads (#2867), the ``LINTRO_AI_*`` overlays that make provider,
    transport and model come from the row, and the credential binding the
    wrapper applies (``LANE_CREDENTIAL_*``, ``LANE_BASE_URL_*``). No check,
    no secret: variable *names* only.

``--phase check``
    Refuse the attempt, before any provider call, when the lane's credential
    is absent (exit 2: red with ``status=no-credential``, never a fallback
    trigger), or when something else could route the run (exit 3): a
    competing credential in the environment or on disk, the provider's
    base-URL override naming anything but the lane endpoint, a file-backed
    override doing the same, or the effective ``ai.*`` config
    (``LINTRO_EFFECTIVE_AI_JSON``, from ``lintro config --json``) still
    differing from the row on provider, transport, model, ``api_base_url``,
    ``api_key_env`` or ``fallback_models``. The wrapper has already captured
    and unset every other chain credential and bound the lane's own under
    the variable its CLI reads, so what this phase sees is what the CLI
    subprocess would see.

Standard library only; the refusal path must not depend on a synced
virtualenv. Every line printed is ``NAME=value`` with a validated name and
no line break, so the wrapper can read it with a plain loop.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

__all__ = ["main"]

#: Exit statuses the wrapper routes: absent credential, and refusal.
EXIT_NO_CREDENTIAL: Final[int] = 2
EXIT_REFUSED: Final[int] = 3

#: Every credential variable a dogfood lane has ever bound, plus the SDK
#: keys the CLIs or lintro could pick up. A set one that is not the active
#: lane's is a competing credential.
CREDENTIAL_VARIABLES: Final[tuple[str, ...]] = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ZAI_AUTH_TOKEN",
    "CODEX_AUTH_JSON",
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
    "CURSOR_API_KEY",
)

#: File-backed credentials, by the credential kind that owns each.
CREDENTIAL_FILES: Final[dict[str, str]] = {
    "codex_oauth": ".codex/auth.json",
}

_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class LaneRefusalError(Exception):
    """The attempt may not run; ``status`` is the exit code."""

    def __init__(self, message: str, *, status: int = EXIT_REFUSED) -> None:
        super().__init__(message)
        self.status = status


def _load_lanes(env: dict[str, str]) -> tuple[list[str], dict[str, Any]]:
    """Parse the chain and the rows the lane job published."""
    try:
        chain = json.loads(env.get("LINTRO_REVIEW_CHAIN", ""))
        lanes = json.loads(env.get("LINTRO_REVIEW_LANES", ""))
    except ValueError as exc:
        msg = f"LINTRO_REVIEW_CHAIN / LINTRO_REVIEW_LANES are not JSON: {exc}"
        raise LaneRefusalError(msg) from exc
    if (
        not isinstance(chain, list)
        or not chain
        or not all(isinstance(entry, str) for entry in chain)
        or not isinstance(lanes, dict)
    ):
        raise LaneRefusalError(
            "LINTRO_REVIEW_CHAIN must be a non-empty list and "
            "LINTRO_REVIEW_LANES an object",
        )
    for entry in chain:
        if not isinstance(lanes.get(entry), dict):
            msg = f"chain entry {entry!r} has no published row"
            raise LaneRefusalError(msg)
    return chain, lanes


def _active(env: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Return the attempt's lane id and row."""
    chain, lanes = _load_lanes(env)
    raw = env.get("LINTRO_REVIEW_ATTEMPT", "0").strip() or "0"
    if not raw.isdigit() or int(raw) >= len(chain):
        msg = f"LINTRO_REVIEW_ATTEMPT={raw!r} is not an index into the chain {chain}"
        raise LaneRefusalError(msg)
    lane_id = chain[int(raw)]
    return lane_id, lanes[lane_id]


def _value(value: Any) -> str:
    """Render a row value as one export line's value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ",".join(value)
    return json.dumps(value, separators=(",", ":"))


#: The keys ``lintro config --json`` must report under ``ai`` for the
#: binding to be verifiable; the check refuses when any is absent.
_EFFECTIVE_KEYS: Final[tuple[str, ...]] = (
    "provider",
    "transport",
    "model",
    "api_base_url",
    "api_key_env",
    "fallback_models",
)
#: Row fields whose value the wrapper uses as a variable NAME. The resolver
#: validated them at workflow_sha; the gate validates them again, so the
#: wrapper's trust in these names is enforced by the script it trusts.
_NAME_VALUED_FIELDS: Final[tuple[str, ...]] = (
    "credential_secret",
    "credential_env",
    "credential_source_env",
    "base_url_env",
)


def _check_names(lane_id: str, row: dict[str, Any]) -> None:
    """Refuse a row whose name-valued fields are not variable names."""
    for field in _NAME_VALUED_FIELDS:
        value = row.get(field)
        if value is not None and (
            not isinstance(value, str) or not _NAME_RE.fullmatch(value)
        ):
            msg = f"lane {lane_id!r} field {field!r} is not a variable name"
            raise LaneRefusalError(msg)


def _exports(lane_id: str, row: dict[str, Any]) -> dict[str, str]:
    """Return the ``--phase env`` lines for a row."""
    _check_names(lane_id, row)
    non_first_party = row.get("first_party") is False
    return {
        "LINTRO_REVIEW_LANE": lane_id,
        "LINTRO_REVIEW_ENDPOINT": _value(row.get("endpoint")),
        "LINTRO_REVIEW_FIRST_PARTY": _value(row.get("first_party")),
        "LINTRO_REVIEW_REPORTS_MODEL_USAGE": _value(row.get("reports_model_usage")),
        "LINTRO_REVIEW_SHADOW_PRICE_MODEL": _value(row.get("shadow_price_model")),
        "LINTRO_AI_PROVIDER": _value(row.get("provider")),
        "LINTRO_AI_TRANSPORT": _value(row.get("transport")),
        "LINTRO_AI_MODEL": _value(row.get("model")),
        "LANE_CREDENTIAL": _value(row.get("credential")),
        "LANE_CREDENTIAL_FORM": _value(row.get("credential_form")),
        "LANE_CREDENTIAL_SECRET": _value(row.get("credential_secret")),
        "LANE_CREDENTIAL_ENV": _value(row.get("credential_env")),
        "LANE_CREDENTIAL_FILE": _value(row.get("credential_file")),
        "LANE_CREDENTIAL_SOURCE_ENV": _value(row.get("credential_source_env")),
        "LANE_BASE_URL_ENV": _value(row.get("base_url_env")),
        # Exported to the endpoint on a non-first-party lane, unset on a
        # first-party one; nothing else sets it (ADR-0011 A).
        "LANE_BASE_URL_VALUE": _value(row.get("endpoint")) if non_first_party else "",
    }


def _check_credential(row: dict[str, Any], env: dict[str, str]) -> None:
    """Refuse when the lane's own credential is absent (exit 2).

    The wrapper has already bound the lane's secret under the variable its
    CLI reads (env form) or re-armed the source variable (file form).
    """
    form = row.get("credential_form")
    source = (
        row.get("credential_env") if form == "env" else row.get("credential_source_env")
    )
    if not isinstance(source, str) or not env.get(source, "").strip():
        msg = (
            f"no provider credential: the lane binds {source!r}, which is unset; "
            "the manifest promised a binding the secrets do not hold"
        )
        raise LaneRefusalError(msg, status=EXIT_NO_CREDENTIAL)


def _check_competing(
    lane_id: str,
    row: dict[str, Any],
    env: dict[str, str],
    home: Path,
) -> None:
    """Refuse a second credential in the environment or on disk."""
    own = {
        name
        for name in (
            row.get("credential_secret"),
            row.get("credential_env"),
            row.get("credential_source_env"),
        )
        if isinstance(name, str)
    }
    competing = [
        name
        for name in CREDENTIAL_VARIABLES
        if name not in own and env.get(name, "").strip()
    ]
    if competing:
        msg = (
            f"lane {lane_id!r} refuses to run beside competing credentials "
            f"{competing}"
        )
        raise LaneRefusalError(msg)
    for kind, relative in CREDENTIAL_FILES.items():
        if row.get("credential") == kind:
            continue
        if (home / relative).is_file():
            msg = (
                f"lane {lane_id!r} refuses to run beside the {kind} session "
                f"at ~/{relative}"
            )
            raise LaneRefusalError(msg)


def _host(url: str) -> str:
    """Return the ``host:port`` an https URL names, or the raw value.

    Only an https URL reduces to its host and port (443 by default); a
    cleartext, schemeless or unparsable value is returned as written, so it
    never equals a lane endpoint, which the resolver requires to be https.
    """
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() != "https" or not parts.hostname:
        return value
    try:
        port = parts.port
    except ValueError:
        return value
    return f"{parts.hostname.lower()}:{port or 443}"


def _check_base_url(
    lane_id: str,
    row: dict[str, Any],
    env: dict[str, str],
    home: Path,
) -> None:
    """Refuse an override that names anything but the lane endpoint."""
    endpoint = str(row.get("endpoint", ""))
    variable = row.get("base_url_env")
    if isinstance(variable, str) and variable:
        current = env.get(variable, "").strip()
        if row.get("first_party") and current:
            msg = (
                f"lane {lane_id!r} is first-party, so {variable} must be unset "
                "(it is set)"
            )
            raise LaneRefusalError(msg)
        if not row.get("first_party") and current and _host(current) != _host(endpoint):
            msg = (
                f"lane {lane_id!r}: {variable} names {current!r}, not the lane "
                f"endpoint {endpoint!r}"
            )
            raise LaneRefusalError(msg)
    for entry in row.get("base_url_files") or []:
        if not isinstance(entry, dict):
            continue
        path = Path(str(entry.get("path", "")).replace("$HOME", str(home)))
        key = str(entry.get("key", ""))
        if not path.is_file() or not key:
            continue
        value = _toml_value(lane_id, path, key)
        if value is not None and _host(value) != _host(endpoint):
            msg = (
                f"lane {lane_id!r}: {path} sets {key} to {value!r}, not the lane "
                f"endpoint {endpoint!r}"
            )
            raise LaneRefusalError(msg)


def _toml_value(lane_id: str, path: Path, key: str) -> str | None:
    """Read one dotted key from a TOML file the CLI reads, failing closed.

    Args:
        lane_id: The lane, for the refusal message.
        path: The file.
        key: A dotted key path.

    Returns:
        The value as a string, or ``None`` when the key is absent.

    Raises:
        LaneRefusalError: When the file is not valid TOML: a file the CLI
            would read and this gate cannot is not a file to run beside.
    """
    try:
        document: Any = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        msg = f"lane {lane_id!r}: {path} could not be read as TOML ({exc})"
        raise LaneRefusalError(msg) from exc
    for part in key.split("."):
        if not isinstance(document, dict) or part not in document:
            return None
        document = document[part]
    return str(document) if document is not None else None


def _check_config(lane_id: str, row: dict[str, Any], env: dict[str, str]) -> None:
    """Refuse an effective ``ai.*`` config that still differs from the row."""
    raw = env.get("LINTRO_EFFECTIVE_AI_JSON", "").strip()
    if not raw:
        return
    try:
        effective = json.loads(raw)
    except ValueError as exc:
        msg = f"LINTRO_EFFECTIVE_AI_JSON is not JSON: {exc}"
        raise LaneRefusalError(msg) from exc
    if not isinstance(effective, dict):
        raise LaneRefusalError("LINTRO_EFFECTIVE_AI_JSON must be an object")
    if "error" in effective:
        msg = f"the effective ai config could not be read: {effective['error']}"
        raise LaneRefusalError(msg)
    # Every compared key must be present: an absent key is not agreement,
    # it is a report this gate does not understand.
    missing = [key for key in _EFFECTIVE_KEYS if key not in effective]
    if missing:
        msg = f"lane {lane_id!r}: effective ai config lacks {missing}"
        raise LaneRefusalError(msg)
    expected = {
        "provider": row.get("provider"),
        "transport": row.get("transport"),
        "model": row.get("model"),
    }
    for field, want in expected.items():
        have = effective.get(field)
        # Every row names a model (the default or the LINTRO_AI_MODEL
        # override, resolved by the lane job), so an empty effective model
        # is a disagreement too.
        if (have or None) != want:
            msg = (
                f"lane {lane_id!r}: effective ai.{field} is {have!r}, the row "
                f"says {want!r}"
            )
            raise LaneRefusalError(msg)
    base_url = effective.get("api_base_url")
    if base_url and _host(str(base_url)) != _host(str(row.get("endpoint", ""))):
        msg = (
            f"lane {lane_id!r}: ai.api_base_url is {base_url!r}, not the lane "
            "endpoint"
        )
        raise LaneRefusalError(msg)
    key_env = effective.get("api_key_env")
    if key_env and key_env != row.get("credential_env"):
        msg = (
            f"lane {lane_id!r}: ai.api_key_env is {key_env!r}, not the lane's "
            f"{row.get('credential_env')!r}"
        )
        raise LaneRefusalError(msg)
    fallbacks = effective.get("fallback_models") or []
    # The dogfood review falls back by lane (ADR-0011 D), never by model: a
    # configured model fallback could name a model the lane's provider does
    # not serve.
    if fallbacks:
        msg = (
            f"lane {lane_id!r}: fallback_models {fallbacks} are set; the review "
            "falls back by lane, not by model"
        )
        raise LaneRefusalError(msg)


def _print(values: dict[str, str]) -> None:
    """Print export lines; a value that could span a line is a refusal."""
    for name, value in values.items():
        if not _NAME_RE.fullmatch(name) or "\n" in value or "\r" in value:
            msg = f"export {name!r} would not be a single line"
            raise LaneRefusalError(msg)
        sys.stdout.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> int:
    """Run one phase.

    Args:
        argv: Arguments, or ``None`` for ``sys.argv``.

    Returns:
        0 when the attempt may proceed; 2 when the lane's credential is
        absent; 3 on any other refusal.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--phase", choices=("env", "check"), required=True)
    args = parser.parse_args(argv)
    env = dict(os.environ)
    home = Path(env["HOME"]) if env.get("HOME") else Path.home()
    try:
        lane_id, row = _active(env)
        if args.phase == "env":
            _print(_exports(lane_id, row))
            return 0
        _check_names(lane_id, row)
        _check_credential(row, env)
        _check_competing(lane_id, row, env, home)
        _check_base_url(lane_id, row, env, home)
        _check_config(lane_id, row, env)
    except LaneRefusalError as exc:
        sys.stderr.write(f"::error::review lane: {exc}\n")
        return exc.status
    sys.stderr.write(
        f"review lane: {lane_id!r} bound; credential {row.get('credential')!r} "
        "present, no competitor\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
