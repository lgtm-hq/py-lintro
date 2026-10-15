#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Resolve the dogfood review chain from the committed lanes manifest (#2869).

ADR-0011 decision A: a lane is a named row in ``scripts/ci/ai_review_lanes.json``
and nowhere else, and the manifest's ordered ``chain`` is the only routing
input. This script runs in a secret-free job, reads the manifest at the commit
that supplied the workflow (``github.workflow_sha``), validates it, and
publishes the chain and every lane's fields as step outputs. It fails closed,
before any secret is exposed, on:

- an empty chain, a duplicate entry, or an entry naming no declared row;
- a row missing a required field or carrying one of the wrong shape;
- a non-``https`` endpoint, an endpoint host missing from the row's own egress
  list, or a wildcard egress host;
- a model outside the row's allowed list;
- a chain mixing ``first_party`` values, or a chain with more than one entry
  whose first entry is non-first-party (decision D);
- a non-first-party row without ``shadow_price_model``, or any row whose
  default or allowed model has no list-price table (``priced_models``) and no
  ``shadow_price_model`` (decision B).

Every error names the lanes the manifest declares. Standard library only: the
job that runs this installs nothing.

Outputs (``--format github`` appends ``name=value`` lines to ``$GITHUB_OUTPUT``,
or to stdout when it is unset; ``--format json`` prints one object):

    chain                 JSON list of lane ids, active lane first
    lane                  the active lane id (``chain[0]``)
    egress                space-separated union of the chain's egress hosts
    lanes                 JSON object: every chain lane's row, keyed by id
    provider, transport, endpoint, first_party, reports_model_usage,
    credential, credential_form, credential_env, credential_file,
    credential_source_env, base_url_env, base_url_files, model, models,
    shadow_price_model, cost_basis
                          the active lane's fields (lists and objects as JSON,
                          booleans as ``true``/``false``, null as ``""``)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NoReturn
from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_MANIFEST",
    "LaneError",
    "ResolvedChain",
    "load_manifest",
    "main",
    "resolve_chain",
]

#: The committed manifest, relative to the repository root.
DEFAULT_MANIFEST: Final[Path] = Path("scripts/ci/ai_review_lanes.json")

#: Cost bases a row may declare (``lintro/ai/enums/cost_basis.py``).
COST_BASES: Final[frozenset[str]] = frozenset(
    {"billed", "estimated", "unpriceable", "list_price_shadow", "unknown"},
)

#: Credential kinds a row may bind (``lintro/ai/enums/credential_kind.py``).
CREDENTIALS: Final[frozenset[str]] = frozenset(
    {"claude_oauth", "api_key", "zai_token", "codex_oauth", "cursor_api_key"},
)

#: Providers and transports lintro runs the dogfood review on.
PROVIDERS: Final[frozenset[str]] = frozenset({"anthropic", "openai", "cursor"})
TRANSPORTS: Final[frozenset[str]] = frozenset({"cli", "api"})

#: Fields every row must carry, with the type each must have.
_REQUIRED: Final[dict[str, type | tuple[type, ...]]] = {
    "provider": str,
    "transport": str,
    "endpoint": str,
    "first_party": bool,
    "reports_model_usage": bool,
    "credential": str,
    "credential_form": str,
    "base_url_env": (str, type(None)),
    "base_url_files": list,
    "egress": list,
    "model": (str, type(None)),
    "models": list,
    "cost_basis": str,
}

#: The active-lane fields published flat, in output order.
_LANE_OUTPUTS: Final[tuple[str, ...]] = (
    "provider",
    "transport",
    "endpoint",
    "first_party",
    "reports_model_usage",
    "credential",
    "credential_form",
    "credential_env",
    "credential_file",
    "credential_source_env",
    "base_url_env",
    "base_url_files",
    "model",
    "models",
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
    """

    chain: list[str]
    lanes: dict[str, dict[str, Any]]
    egress: list[str]

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
        document = json.loads(path.read_text(encoding="utf-8"))
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
    for field, kind in _REQUIRED.items():
        if field not in row:
            _fail(f"lane {lane_id!r} is missing {field!r}", lanes)
        if not isinstance(row[field], kind) or (
            kind is bool and type(row[field]) is not bool
        ):
            _fail(f"lane {lane_id!r} field {field!r} has the wrong type", lanes)
    for field in ("egress", "models"):
        if not all(isinstance(item, str) and item for item in row[field]):
            _fail(f"lane {lane_id!r} field {field!r} must list strings", lanes)
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
    return row


def _check_credential(lane_id: str, row: dict[str, Any], lanes: dict[str, Any]) -> None:
    """Check the credential's declared form names where it comes from."""
    form = row["credential_form"]
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


def _check_endpoint(lane_id: str, row: dict[str, Any], lanes: dict[str, Any]) -> None:
    """Check the endpoint is https and reachable through the row's own egress."""
    parts = urlsplit(row["endpoint"])
    if parts.scheme != "https" or not parts.hostname:
        _fail(f"lane {lane_id!r} endpoint must be an https URL", lanes)
    hosts: list[str] = []
    for entry in row["egress"]:
        host, sep, port = entry.rpartition(":")
        if not sep or not host or not port.isdigit():
            _fail(
                f"lane {lane_id!r} egress entry {entry!r} must be host:port",
                lanes,
            )
        if "*" in host:
            _fail(
                f"lane {lane_id!r} declares a wildcard egress host {host!r}",
                lanes,
            )
        hosts.append(host)
    if parts.hostname not in hosts:
        _fail(
            f"lane {lane_id!r} endpoint host {parts.hostname!r} is "
            "missing from its egress",
            lanes,
        )


def _check_models(
    lane_id: str,
    row: dict[str, Any],
    priced: set[str],
    lanes: dict[str, Any],
) -> None:
    """Check the model policy and the shadow-pricing rule (decision B)."""
    model = row["model"]
    models = row["models"]
    if model is not None and model not in models:
        _fail(
            f"lane {lane_id!r} default model {model!r} is outside its allowed models",
            lanes,
        )
    if model is None and models:
        _fail(f"lane {lane_id!r} lists allowed models but no default", lanes)
    if model is None and row["reports_model_usage"]:
        _fail(
            f"lane {lane_id!r} reports model usage but names no model to verify",
            lanes,
        )
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
    unpriced = [m for m in models if m not in priced]
    if unpriced and shadow is None:
        _fail(
            f"lane {lane_id!r} allows unpriced models {unpriced} and "
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


def resolve_chain(document: dict[str, Any]) -> ResolvedChain:
    """Validate the manifest and return the chain it routes to.

    Args:
        document: The parsed manifest.

    Returns:
        The validated chain with every row it names.

    Raises:
        LaneError: On any of the refusals in the module docstring.
    """
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
        if not isinstance(lane_id, str) or not lane_id:
            _fail("lane ids must be non-empty strings", lanes_raw)
        checked = _check_shape(lane_id, row, lanes_raw)
        _check_credential(lane_id, checked, lanes_raw)
        _check_endpoint(lane_id, checked, lanes_raw)
        _check_models(lane_id, checked, priced, lanes_raw)
        rows[lane_id] = checked
    chain = _check_chain(document.get("chain"), rows)
    lanes = {entry: rows[entry] for entry in chain}
    return ResolvedChain(chain=chain, lanes=lanes, egress=_egress_union(chain, lanes))


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
        "egress": " ".join(resolved.egress),
        "lanes": _output_value(resolved.lanes),
    }
    for field in _LANE_OUTPUTS:
        values[field] = _output_value(active.get(field))
    return values


def _write_outputs(values: dict[str, str]) -> None:
    """Append step outputs; every value is single-line by construction."""
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
        resolved = resolve_chain(load_manifest(args.manifest))
    except LaneError as exc:
        sys.stderr.write(f"::error::review lanes: {exc}\n")
        return 1
    values = outputs_for(resolved)
    if args.format == "json":
        sys.stdout.write(json.dumps(values, indent=2) + "\n")
    else:
        _write_outputs(values)
    sys.stderr.write(
        f"review lanes: active {resolved.chain[0]!r}, chain {resolved.chain}, "
        f"egress {resolved.egress}\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
