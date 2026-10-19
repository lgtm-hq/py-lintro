#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Resolve the dogfood review chain from the committed lanes manifest (#2869).

ADR-0011 decision A: a lane is a named row in ``scripts/ci/ai_review_lanes.json``
and nowhere else. Which row a run uses is selected by the repository variable
``LINTRO_AI_PROVIDER``, which this script reads from its environment: it must
name a declared row, unset means the manifest's ``chain[0]``, and anything
else is a refusal. ``LINTRO_AI_MODEL`` overrides the selected row's default
model; unset means the row's own. The manifest's ordered ``chain`` is the
default lane followed by the fallback order of decision D; a selected lane
other than ``chain[0]`` runs as a single-entry chain. This script runs in a
secret-free job, reads the manifest at the commit that supplied the workflow
(``github.workflow_sha``), validates it and the two variables, and publishes
the chain and every lane's fields as step outputs. It fails closed, before
any secret is exposed, on:

- a selector that names no declared row, or a model override that is not a
  model id;
- an empty chain, a duplicate entry, or an entry naming no declared row;
- a row missing a required field or carrying one of the wrong shape;
- a lane id or a variable name of the wrong shape (they become step outputs
  and job names, so a line break or a shell metacharacter in one must never
  reach the workflow);
- a non-``https`` endpoint, an endpoint host and port missing from the row's
  own egress list, an egress port out of range, or a wildcard egress host;
- a row that names no default model: every run asks for a model this
  repository chose, never a CLI's own default;
- a ``first_party`` flag that disagrees with the endpoint host (each
  provider is first-party on exactly one host), a non-first-party row whose
  cost basis is not ``unknown``, or a credential bound in the wrong form;
- a duplicate or unknown key, in the manifest or in a row;
- a chain mixing ``first_party`` values, or a chain with more than one entry
  whose first entry is non-first-party (decision D);
- a non-first-party row without ``shadow_price_model``, or any row whose
  default model has no list-price table (``priced_models``) and no
  ``shadow_price_model`` (decision B);
- a row without ``smoke_row``, a ``null`` smoke row without a ``smoke_gap``,
  a named smoke row without a ``smoke_covers`` level or with a gap as well,
  or a gap with a coverage level (#2873).

Every error names the lanes the manifest declares. Standard library only: the
job that runs this installs nothing.

Outputs (``--format github`` appends ``name=value`` lines to ``$GITHUB_OUTPUT``,
or to stdout when it is unset; ``--format json`` prints one object):

    chain                 JSON list of lane ids, active lane first
    lane                  the active lane id (``chain[0]``)
    model_source          ``variable`` when LINTRO_AI_MODEL set the active
                          lane's model, ``manifest`` when the row's default
                          applies
    egress                space-separated union of the chain's egress hosts
    egress_all            the same union over every declared row, for a job
                          that drives every lane (the Tier 2 contract smoke)
    lanes                 JSON object: every chain lane's row, keyed by id
    provider, transport, endpoint, first_party, reports_model_usage,
    credential, credential_secret, credential_form, credential_env,
    credential_file,
    credential_source_env, base_url_env, base_url_files, model,
    shadow_price_model, cost_basis
                          the active lane's fields (lists and objects as JSON,
                          booleans as ``true``/``false``, null as ``""``)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NoReturn
from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_MANIFEST",
    "LaneError",
    "MODEL_ENV",
    "SELECTOR_ENV",
    "ResolvedChain",
    "load_manifest",
    "main",
    "resolve_chain",
]

#: The committed manifest, relative to the repository root.
DEFAULT_MANIFEST: Final[Path] = Path("scripts/ci/ai_review_lanes.json")

#: The repository variable that selects a declared lane by id (the lane job
#: forwards ``vars.LINTRO_AI_PROVIDER`` under this name) and the one that
#: overrides the selected row's default model. Nothing else routes.
SELECTOR_ENV: Final[str] = "LINTRO_AI_PROVIDER"
MODEL_ENV: Final[str] = "LINTRO_AI_MODEL"

#: Cost bases a row may declare (``lintro/ai/enums/cost_basis.py``).
COST_BASES: Final[frozenset[str]] = frozenset(
    {"billed", "estimated", "unpriceable", "list_price_shadow", "unknown"},
)

#: Credential kinds a row may bind (``lintro/ai/enums/credential_kind.py``).
CREDENTIALS: Final[frozenset[str]] = frozenset(
    {
        "claude_oauth",
        "api_key",
        "zai_token",
        "kimi_token",
        "codex_oauth",
        "cursor_api_key",
    },
)

#: Providers and transports lintro runs the dogfood review on.
PROVIDERS: Final[frozenset[str]] = frozenset({"anthropic", "openai", "cursor"})

#: Each provider's own host. ``first_party`` must agree with the endpoint:
#: decision D's chain shape and decision B's verification states both rest
#: on it, so a row cannot simply assert it.
FIRST_PARTY_HOSTS: Final[dict[str, str]] = {
    "anthropic": "api.anthropic.com",
    "openai": "api.openai.com",
    "cursor": "api2.cursor.sh",
}

#: The gateway host each gateway credential kind is bound to: the run record
#: names a bearer by the host it goes to (``lintro.ai.provenance``), so a row
#: may not pair a kind with another gateway's host.
GATEWAY_HOSTS: Final[dict[str, str]] = {
    "zai_token": "api.z.ai",  # nosec B105 - a hostname, not a secret
    "kimi_token": "api.moonshot.ai",  # nosec B105 - a hostname, not a secret
}

#: The form each credential kind takes.
BINDING_FORMS: Final[dict[str, str]] = {
    "claude_oauth": "env",
    "api_key": "env",
    "zai_token": "env",  # nosec B105 - a form name, not a secret
    "kimi_token": "env",  # nosec B105 - a form name, not a secret
    "codex_oauth": "file",
    "cursor_api_key": "env",
}

#: Keys a manifest and a row may carry; anything else is a mistake a reader
#: could take for routing (the issue's original ``default`` key, say).
TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {"$comment", "chain", "priced_models", "lanes"},
)
OPTIONAL_ROW_KEYS: Final[frozenset[str]] = frozenset(
    {
        "credential_env",
        "credential_file",
        "credential_source_env",
        "shadow_price_model",
        # Each exactly as _check_smoke allows for the row's smoke_row value.
        "smoke_gap",
        "smoke_covers",
    },
)

#: What a lane's smoke row proves (#2873). ``route``: the lane's own secret
#: down the lane's own path. ``host``: the provider's metered key to the host
#: and path the CLI calls, because the lane's credential is a session the raw
#: API cannot take. ``provider``: the session's own route is unreachable by
#: an API smoke, so only the provider's API is proved to answer.
SMOKE_COVERS: Final[frozenset[str]] = frozenset({"route", "host", "provider"})
TRANSPORTS: Final[frozenset[str]] = frozenset({"cli", "api"})

#: A lane id: lowercase, digits and hyphens, so it can name a job, a
#: commit-status context and an output without escaping.
_LANE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: One DNS label of an egress host, lowercased: no empty label, no label
#: starting or ending with a hyphen, at most 63 characters.
_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

#: An environment-variable or secret name.
_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")

#: A model id as the provider CLIs accept one (``gpt-6.1-sol``,
#: ``cursor-grok-4.6-high``, ``kimi-k3[1m]``): no whitespace, no shell
#: metacharacter, so it can travel as an output and an export.
_MODEL_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,127}$",
)

#: Free-form string fields: published flat and read by a shell, so no
#: whitespace or control character may appear in them.
_TOKEN_FIELDS: Final[tuple[str, ...]] = (
    "endpoint",
    "credential_file",
    "model",
    "shadow_price_model",
)

#: Fields whose value must be a variable or secret name when present.
_NAME_FIELDS: Final[tuple[str, ...]] = (
    "credential_secret",
    "credential_env",
    "credential_source_env",
    "base_url_env",
)

#: Fields every row must carry, with the type each must have.
_REQUIRED: Final[dict[str, type | tuple[type, ...]]] = {
    "provider": str,
    "transport": str,
    "endpoint": str,
    "first_party": bool,
    "reports_model_usage": bool,
    "credential": str,
    "credential_secret": str,
    "credential_form": str,
    "base_url_env": (str, type(None)),
    "base_url_files": list,
    "egress": list,
    "model": str,
    "cost_basis": str,
    # The smoke row that stands for the lane, or null with a smoke_gap (#2873).
    "smoke_row": (str, type(None)),
}

#: The active-lane fields published flat, in output order.
_LANE_OUTPUTS: Final[tuple[str, ...]] = (
    "provider",
    "transport",
    "endpoint",
    "first_party",
    "reports_model_usage",
    "credential",
    "credential_secret",
    "credential_form",
    "credential_env",
    "credential_file",
    "credential_source_env",
    "base_url_env",
    "base_url_files",
    "model",
    "shadow_price_model",
    "cost_basis",
)


class LaneError(ValueError):
    """The manifest cannot be resolved; the message names the allowed lanes."""


@dataclass(frozen=True)
class ResolvedChain:
    """The validated chain and the rows it names.

    Attributes:
        chain: Lane ids, active lane first.
        lanes: The chain's rows, keyed by id, in chain order.
        egress: The union of the chain's egress hosts, in first-seen order.
        egress_all: The union of every declared row's egress hosts, for a
            job that drives every lane at once (the Tier 2 contract smoke).
        model_source: ``variable`` when ``LINTRO_AI_MODEL`` set the active
            lane's model, ``manifest`` when the row's default applies.
    """

    chain: list[str]
    lanes: dict[str, dict[str, Any]]
    egress: list[str]
    egress_all: list[str]
    model_source: str = "manifest"

    @property
    def active(self) -> dict[str, Any]:
        """Return the active lane's row."""
        return self.lanes[self.chain[0]]


def _egress_union(chain: list[str], lanes: dict[str, dict[str, Any]]) -> list[str]:
    """Return the chain's egress hosts, deduplicated in first-seen order."""
    seen: dict[str, None] = {}
    for lane_id in chain:
        for host in lanes[lane_id]["egress"]:
            seen.setdefault(host, None)
    return list(seen)


def load_manifest(path: Path) -> dict[str, Any]:
    """Read and parse the manifest.

    Args:
        path: The manifest file.

    Returns:
        The parsed document.

    Raises:
        LaneError: When the file is missing, not JSON, or not an object.
    """
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_keys,
        )
    except FileNotFoundError as exc:
        msg = f"lanes manifest not found: {path}"
        raise LaneError(msg) from exc
    except ValueError as exc:
        msg = f"lanes manifest is not valid JSON: {path}: {exc}"
        raise LaneError(msg) from exc
    if not isinstance(document, dict):
        msg = f"lanes manifest must be a JSON object: {path}"
        raise LaneError(msg)
    return document


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build an object, refusing a key that appears twice.

    ``json.loads`` keeps the last of two same-named lanes silently; a
    duplicate is a manifest mistake a reviewer must see.

    Args:
        pairs: The object's key/value pairs in document order.

    Returns:
        The object.

    Raises:
        ValueError: When a key repeats.
    """
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            msg = f"duplicate key {key!r}"
            raise ValueError(msg)
        result[key] = value
    return result


def _allowed(lanes: dict[str, Any]) -> str:
    """Render the declared lane ids for an error message."""
    names = ", ".join(sorted(lanes)) if lanes else "(none declared)"
    return f"declared lanes: {names}"


def _fail(message: str, lanes: dict[str, Any]) -> NoReturn:
    """Refuse, naming the allowed lanes.

    Args:
        message: What is wrong with the manifest.
        lanes: The declared rows, whose ids the message ends with.

    Raises:
        LaneError: Always.
    """
    raise LaneError(f"{message}; {_allowed(lanes)}")


def _check_shape(lane_id: str, row: Any, lanes: dict[str, Any]) -> dict[str, Any]:
    """Check a row carries every required field with the right shape."""
    if not isinstance(row, dict):
        _fail(f"lane {lane_id!r} must be a JSON object", lanes)
    unknown = sorted(set(row) - set(_REQUIRED) - OPTIONAL_ROW_KEYS)
    if unknown:
        _fail(f"lane {lane_id!r} carries unknown fields {unknown}", lanes)
    for field, kind in _REQUIRED.items():
        if field not in row:
            _fail(f"lane {lane_id!r} is missing {field!r}", lanes)
        if not isinstance(row[field], kind) or (
            kind is bool and type(row[field]) is not bool
        ):
            _fail(f"lane {lane_id!r} field {field!r} has the wrong type", lanes)
    if not all(isinstance(item, str) and item for item in row["egress"]):
        _fail(f"lane {lane_id!r} field 'egress' must list strings", lanes)
    _check_smoke(lane_id, row, lanes)
    _check_names(lane_id, row, lanes)
    return row


def _check_smoke(lane_id: str, row: dict[str, Any], lanes: dict[str, Any]) -> None:
    """Check the row names its weekly smoke row, or says why none can (#2873).

    ``smoke_row`` is the name of the provider-smoke table row that stands for
    this lane, with ``smoke_covers`` saying what that call proves
    (:data:`SMOKE_COVERS`); ``null`` is allowed only with a ``smoke_gap`` that
    says why no API smoke can reach the lane. The agreement between the row
    and the lane (host, protocol, credential, level) is held by
    ``tests/scripts/ci/test_smoke_rows_match_lanes.py``, which reads both
    files; this check only keeps the declaration well formed. Presence and
    shape of ``smoke_row`` itself are the shape check's (``_REQUIRED``).
    """
    smoke_row = row["smoke_row"]
    gap = row.get("smoke_gap")
    covers = row.get("smoke_covers")
    if smoke_row is None:
        if not isinstance(gap, str) or not gap.strip():
            _fail(
                f"lane {lane_id!r} has no smoke row and no 'smoke_gap' saying why",
                lanes,
            )
        if covers is not None:
            _fail(f"lane {lane_id!r} has no smoke row but a 'smoke_covers'", lanes)
        return
    if not isinstance(smoke_row, str) or not _LANE_ID_RE.fullmatch(smoke_row):
        _fail(f"lane {lane_id!r} field 'smoke_row' is not a smoke row name", lanes)
    if gap is not None:
        _fail(
            f"lane {lane_id!r} names a smoke row and a 'smoke_gap'; it cannot be both",
            lanes,
        )
    if not isinstance(covers, str) or covers not in SMOKE_COVERS:
        _fail(
            f"lane {lane_id!r} field 'smoke_covers' must be one of "
            f"{sorted(SMOKE_COVERS)}",
            lanes,
        )


def _check_names(lane_id: str, row: dict[str, Any], lanes: dict[str, Any]) -> None:
    """Check the row's enumerated values and the shape of its variable names."""
    if row["provider"] not in PROVIDERS:
        _fail(
            f"lane {lane_id!r} names unknown provider {row['provider']!r}",
            lanes,
        )
    if row["transport"] not in TRANSPORTS:
        _fail(
            f"lane {lane_id!r} names unknown transport {row['transport']!r}",
            lanes,
        )
    if row["credential"] not in CREDENTIALS:
        _fail(
            f"lane {lane_id!r} binds unknown credential {row['credential']!r}",
            lanes,
        )
    if row["cost_basis"] not in COST_BASES:
        _fail(
            f"lane {lane_id!r} declares unknown cost basis {row['cost_basis']!r}",
            lanes,
        )
    for field in _NAME_FIELDS:
        value = row.get(field)
        if value is not None and (
            not isinstance(value, str) or not _NAME_RE.fullmatch(value)
        ):
            _fail(f"lane {lane_id!r} field {field!r} must be a variable name", lanes)
    tokens = [(field, row.get(field)) for field in _TOKEN_FIELDS]
    tokens += [
        ("base_url_files", entry.get(key))
        for entry in row["base_url_files"]
        if isinstance(entry, dict)
        for key in ("path", "key")
    ]
    for field, value in tokens:
        if isinstance(value, str) and (
            not value or any(c.isspace() or not c.isprintable() for c in value)
        ):
            _fail(
                f"lane {lane_id!r} field {field!r} may carry no whitespace or "
                "control character",
                lanes,
            )


def _check_credential(lane_id: str, row: dict[str, Any], lanes: dict[str, Any]) -> None:
    """Check the credential's declared form names where it comes from."""
    form = row["credential_form"]
    expected = BINDING_FORMS[row["credential"]]
    if form != expected:
        _fail(
            f"lane {lane_id!r} credential {row['credential']!r} takes the "
            f"{expected!r} form, not {form!r}",
            lanes,
        )
    stray = {
        "env": ("credential_file", "credential_source_env"),
        "file": ("credential_env",),
    }
    present = [field for field in stray.get(form, ()) if field in row]
    if present:
        _fail(
            f"lane {lane_id!r} binds a {form!r} credential but carries {present}",
            lanes,
        )
    if form == "env":
        if not isinstance(row.get("credential_env"), str) or not row["credential_env"]:
            _fail(
                f"lane {lane_id!r} binds an env credential without 'credential_env'",
                lanes,
            )
    elif form == "file":
        for field in ("credential_file", "credential_source_env"):
            if not isinstance(row.get(field), str) or not row[field]:
                _fail(
                    f"lane {lane_id!r} binds a file credential without {field!r}",
                    lanes,
                )
    else:
        _fail(f"lane {lane_id!r} credential form must be 'env' or 'file'", lanes)
    for entry in row["base_url_files"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("key"), str)
        ):
            _fail(
                f"lane {lane_id!r} 'base_url_files' entries need 'path' and 'key'",
                lanes,
            )


def _is_hostname(host: str) -> bool:
    """Return whether *host* is a DNS name: dot-separated valid labels."""
    return 0 < len(host) <= 253 and all(
        _LABEL_RE.fullmatch(label) for label in host.split(".")
    )


def _check_first_party(
    lane_id: str,
    row: dict[str, Any],
    hostname: str,
    lanes: dict[str, Any],
) -> None:
    """Check ``first_party`` agrees with the endpoint, and the basis with it.

    Decision D's chain shape and decision B's verification states both rest
    on ``first_party``, so a row cannot simply assert it: each provider is
    first-party on exactly one host, and a non-first-party row's figures are
    priced on the wrong table, so its basis is ``unknown``.
    """
    own_host = FIRST_PARTY_HOSTS[row["provider"]]
    if row["first_party"] != (hostname == own_host):
        _fail(
            f"lane {lane_id!r} first_party must be {hostname == own_host} for "
            f"endpoint host {hostname!r} ({row['provider']} is first-party only "
            f"on {own_host!r})",
            lanes,
        )
    gateway_host = GATEWAY_HOSTS.get(row["credential"])
    if gateway_host is not None and hostname != gateway_host:
        _fail(
            f"lane {lane_id!r} binds {row['credential']!r}, which belongs to "
            f"{gateway_host!r}, on endpoint host {hostname!r}",
            lanes,
        )
    if not row["first_party"] and row["cost_basis"] != "unknown":
        _fail(
            f"lane {lane_id!r} is not first-party, so its cost basis must be "
            f"'unknown', not {row['cost_basis']!r}",
            lanes,
        )


def _check_endpoint(lane_id: str, row: dict[str, Any], lanes: dict[str, Any]) -> None:
    """Check the endpoint is https and reachable through the row's own egress.

    The endpoint's host *and port* (443 when the URL names none) must appear
    in the row's egress list; an allowed host on another port cannot reach it.
    """
    try:
        parts = urlsplit(row["endpoint"])
    except ValueError:
        _fail(f"lane {lane_id!r} endpoint is not a valid URL", lanes)
    if parts.scheme != "https" or not parts.hostname:
        _fail(f"lane {lane_id!r} endpoint must be an https URL", lanes)
    try:
        endpoint_port = 443 if parts.port is None else parts.port
    except ValueError:
        _fail(f"lane {lane_id!r} endpoint port is out of range", lanes)
    if not 1 <= endpoint_port <= 65535:
        _fail(f"lane {lane_id!r} endpoint port is out of range", lanes)
    _check_first_party(lane_id, row, parts.hostname, lanes)
    hosts: list[str] = []
    for entry in row["egress"]:
        host, sep, port = entry.rpartition(":")
        host = host.lower()
        if (
            not sep
            or not host
            or not (port.isascii() and port.isdigit())
            or not 1 <= int(port) <= 65535
        ):
            _fail(
                f"lane {lane_id!r} egress entry {entry!r} must be host:port",
                lanes,
            )
        if "*" in host:
            _fail(
                f"lane {lane_id!r} declares a wildcard egress host {host!r}",
                lanes,
            )
        if not _is_hostname(host):
            _fail(f"lane {lane_id!r} egress host {host!r} is not a hostname", lanes)
        hosts.append(f"{host}:{int(port)}")
    # The normalised entries are what the chain's egress union publishes.
    row["egress"] = hosts
    if f"{parts.hostname}:{endpoint_port}" not in hosts:
        _fail(
            f"lane {lane_id!r} endpoint {parts.hostname}:{endpoint_port} is "
            "missing from its egress",
            lanes,
        )


def _check_models(
    lane_id: str,
    row: dict[str, Any],
    priced: set[str],
    lanes: dict[str, Any],
) -> None:
    """Check the default model and the shadow-pricing rule (decision B)."""
    model = row["model"]
    if not _MODEL_RE.fullmatch(model):
        _fail(f"lane {lane_id!r} default model {model!r} is not a model id", lanes)
    shadow = row.get("shadow_price_model")
    if shadow is not None and (not isinstance(shadow, str) or not shadow):
        _fail(
            f"lane {lane_id!r} 'shadow_price_model' must be a non-empty string",
            lanes,
        )
    if not row["first_party"] and shadow is None:
        _fail(
            f"lane {lane_id!r} is not first-party and declares no shadow_price_model",
            lanes,
        )
    if model not in priced and shadow is None:
        _fail(
            f"lane {lane_id!r} names unpriced model {model!r} and "
            "declares no shadow_price_model",
            lanes,
        )
    if shadow is not None and shadow not in priced:
        _fail(
            f"lane {lane_id!r} shadow_price_model {shadow!r} has no list-price table",
            lanes,
        )


def _check_chain(chain: Any, lanes: dict[str, Any]) -> list[str]:
    """Check the chain names declared rows and obeys decision D's shape."""
    if not isinstance(chain, list) or not chain:
        _fail("chain must be a non-empty list of lane ids", lanes)
    if not all(isinstance(entry, str) for entry in chain):
        _fail("chain entries must be lane ids", lanes)
    if len(set(chain)) != len(chain):
        _fail("chain repeats a lane", lanes)
    for entry in chain:
        if entry not in lanes:
            _fail(f"chain names undeclared lane {entry!r}", lanes)
    first_party = {bool(lanes[entry]["first_party"]) for entry in chain}
    if len(first_party) > 1:
        _fail("chain mixes first-party and non-first-party lanes", lanes)
    if not lanes[chain[0]]["first_party"] and len(chain) > 1:
        _fail(
            "a non-first-party lane may be active only as a single-entry chain",
            lanes,
        )
    return list(chain)


def _select(
    chain: list[str],
    rows: dict[str, dict[str, Any]],
    selector: str | None,
) -> list[str]:
    """Apply the lane selector to the manifest's chain.

    Unset keeps the manifest's chain. The default lane keeps its declared
    fallbacks; any other declared lane runs as a single-entry chain, so the
    operator's choice is exactly what runs and decision D's shape holds.
    """
    if not selector:
        return chain
    if not _LANE_ID_RE.fullmatch(selector) or selector not in rows:
        _fail(f"{SELECTOR_ENV}={selector!r} names no declared lane", rows)
    return chain if selector == chain[0] else [selector]


def resolve_chain(
    document: dict[str, Any],
    *,
    selector: str | None = None,
    model: str | None = None,
) -> ResolvedChain:
    """Validate the manifest and return the chain it routes to.

    Args:
        document: The parsed manifest.
        selector: ``LINTRO_AI_PROVIDER``: a declared lane id, or empty for
            the manifest's default.
        model: ``LINTRO_AI_MODEL``: a model id that overrides the selected
            row's default, or empty for the row's own.

    Returns:
        The validated chain with every row it names.

    Raises:
        LaneError: On any of the refusals in the module docstring.
    """
    stray = sorted(set(document) - TOP_LEVEL_KEYS)
    if stray:
        declared = document.get("lanes")
        names = sorted(declared) if isinstance(declared, dict) else []
        msg = (
            f"manifest carries unknown keys {stray}; declared lanes: "
            f"{', '.join(names) or '(none)'}"
        )
        raise LaneError(msg)
    lanes_raw = document.get("lanes")
    if not isinstance(lanes_raw, dict) or not lanes_raw:
        raise LaneError(
            "manifest must declare a non-empty 'lanes' object; declared lanes: (none)",
        )
    priced_raw = document.get("priced_models", [])
    if not isinstance(priced_raw, list) or not all(
        isinstance(m, str) for m in priced_raw
    ):
        _fail("'priced_models' must be a list of model ids", lanes_raw)
    priced = set(priced_raw)
    rows: dict[str, dict[str, Any]] = {}
    for lane_id, row in lanes_raw.items():
        if not isinstance(lane_id, str) or not _LANE_ID_RE.fullmatch(lane_id):
            _fail("lane ids must be lowercase letters, digits and hyphens", lanes_raw)
        checked = _check_shape(lane_id, row, lanes_raw)
        _check_credential(lane_id, checked, lanes_raw)
        _check_endpoint(lane_id, checked, lanes_raw)
        _check_models(lane_id, checked, priced, lanes_raw)
        rows[lane_id] = checked
    chain = _select(_check_chain(document.get("chain"), rows), rows, selector)
    model_source = "manifest"
    if model:
        if not _MODEL_RE.fullmatch(model):
            _fail(f"{MODEL_ENV}={model!r} is not a model id", rows)
        rows[chain[0]] = {**rows[chain[0]], "model": model}
        # Decision B's priced-or-shadow rule applies to the model that runs.
        _check_models(chain[0], rows[chain[0]], priced, rows)
        model_source = "variable"
    lanes = {entry: rows[entry] for entry in chain}
    return ResolvedChain(
        chain=chain,
        lanes=lanes,
        egress=_egress_union(chain, lanes),
        egress_all=_egress_union(list(rows), rows),
        model_source=model_source,
    )


def _output_value(value: Any) -> str:
    """Render one output value on a single line."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"))


def outputs_for(resolved: ResolvedChain) -> dict[str, str]:
    """Return the step outputs for a resolved chain.

    Args:
        resolved: The validated chain.

    Returns:
        Output names and single-line values.
    """
    active = resolved.active
    values: dict[str, str] = {
        "chain": _output_value(resolved.chain),
        "lane": resolved.chain[0],
        "model_source": resolved.model_source,
        "egress": " ".join(resolved.egress),
        "egress_all": " ".join(resolved.egress_all),
        "lanes": _output_value(resolved.lanes),
    }
    for field in _LANE_OUTPUTS:
        values[field] = _output_value(active.get(field))
    return values


def _write_outputs(values: dict[str, str]) -> None:
    """Append step outputs; every value is single-line by construction.

    Args:
        values: Output names and values.

    Raises:
        LaneError: When a value carries a line break, which would let it
            inject a second output; the shape checks refuse such a value
            earlier, and this guard keeps that true whatever is added later.
    """
    for name, value in values.items():
        if "\n" in value or "\r" in value:
            msg = (
                f"output {name!r} would span more than one line; "
                "declared lanes: (refused)"
            )
            raise LaneError(msg)
    lines = "".join(f"{name}={value}\n" for name, value in values.items())
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        sys.stdout.write(lines)


def main(argv: list[str] | None = None) -> int:
    """Resolve the chain and publish it.

    Args:
        argv: Arguments, or ``None`` for ``sys.argv``.

    Returns:
        Process exit status: 0 on success, 1 on a refusal.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="path to the lanes manifest (default: %(default)s)",
    )
    parser.add_argument(
        "--format",
        choices=("github", "json"),
        default="github",
        help="step outputs (github) or one JSON object on stdout (json)",
    )
    args = parser.parse_args(argv)
    try:
        resolved = resolve_chain(
            load_manifest(args.manifest),
            selector=os.environ.get(SELECTOR_ENV, "").strip(),
            model=os.environ.get(MODEL_ENV, "").strip(),
        )
    except LaneError as exc:
        sys.stderr.write(f"::error::review lanes: {exc}\n")
        return 1
    values = outputs_for(resolved)
    try:
        if args.format == "json":
            sys.stdout.write(json.dumps(values, indent=2) + "\n")
        else:
            _write_outputs(values)
    except LaneError as exc:
        sys.stderr.write(f"::error::review lanes: {exc}\n")
        return 1
    sys.stderr.write(
        f"review lanes: active {resolved.chain[0]!r}, chain {resolved.chain}, "
        f"model {resolved.active['model']!r} ({resolved.model_source}), "
        f"egress {resolved.egress}\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
