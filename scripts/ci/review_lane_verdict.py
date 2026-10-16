#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Decide whether a produced review contradicts its lane (ADR-0011 C, #2871).

``scripts/ci/classify_review_outcome.py`` calls this after a review was
produced. The lane facts come from the environment the wrapper exported
(``LINTRO_REVIEW_CHAIN``, ``LINTRO_REVIEW_LANES``, ``LANE_CREDENTIAL``,
``LINTRO_REVIEW_STATE_DIR``) and the provenance from the run record lintro
persisted for this round (the last entry of ``runs`` in ``state.json``).
Outside a lane run, where the wrapper exported nothing, there is nothing to
contradict and the outcome is left alone.

The rules are ADR-0011 decision C's verdict table, for the rows a single
attempt can produce (decision D's chain-walk rows are #2872):

- the recorded ``lane`` is not in the resolved chain, or the recorded
  endpoint host differs from the manifest endpoint of the recorded lane:
  red, ``status=endpoint-mismatch``;
- the recorded lane is in the chain but is not ``chain[0]``: verdict
  unchanged, a ``::warning::`` names the fallback;
- ``model_verification == mismatch`` on a first-party lane: red,
  ``status=model-mismatch``;
- ``model_verification == unverifiable`` on a non-first-party lane: verdict
  unchanged, a ``::warning::`` names the lane and the endpoint;
- ``model_verification == unreported`` on a first-party lane whose row
  declares ``reports_model_usage: true``: red, ``status=model-unreported``;
  with ``reports_model_usage: false`` the verdict is unchanged;
- ``verified``: unchanged, no label.

A produced review with no run record at all is red with
``status=provenance-unrecorded``: the lane cannot be verified, and a check
that cannot verify where the review went must not read as a pass.

An authentication failure is labelled by the credential kind the lane
bound, never by a fixed label, so a gateway 401 is not blamed on the Claude
session. Standard library only: the classifier runs from a bare ``python3``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

__all__ = [
    "AUTH_LABELS",
    "STATUS_ENDPOINT_MISMATCH",
    "STATUS_MODEL_MISMATCH",
    "STATUS_MODEL_UNREPORTED",
    "STATUS_PROVENANCE_UNRECORDED",
    "LaneFacts",
    "LaneVerdict",
    "lane_facts",
    "lane_verdict",
    "latest_run_record",
]

STATUS_ENDPOINT_MISMATCH: Final[str] = "endpoint-mismatch"
STATUS_MODEL_MISMATCH: Final[str] = "model-mismatch"
STATUS_MODEL_UNREPORTED: Final[str] = "model-unreported"
STATUS_PROVENANCE_UNRECORDED: Final[str] = "provenance-unrecorded"

#: The failure label an authentication failure carries, by the credential
#: kind the lane bound (``lintro.ai.enums.credential_kind``). A kind not
#: listed here keeps the transport's generic label.
AUTH_LABELS: Final[dict[str, str]] = {
    "claude_oauth": "auth_failed:oauth_session",
    "codex_oauth": "auth_failed:codex_session",
    "api_key": "auth_failed:key",
    "cursor_api_key": "auth_failed:key",
    "zai_token": "auth_failed:gateway_token",  # nosec B105 - a label
    "kimi_token": "auth_failed:gateway_token",  # nosec B105 - a label
}

#: The file lintro refreshes with this round's run record (state_store).
STATE_FILENAME: Final[str] = "state.json"


@dataclass(frozen=True)
class LaneFacts:
    """What the wrapper exported about the resolved lanes.

    Attributes:
        chain: The resolved chain, active lane first; empty when the
            exported value could not be read, which refuses every recorded
            lane rather than trusting it.
        lanes: The chain's manifest rows, keyed by lane id.
        credential: The credential kind the active lane bound, or empty.
        state_file: The state file holding this round's run record, or
            ``None`` when no state directory was exported.
    """

    chain: tuple[str, ...]
    lanes: Mapping[str, Mapping[str, Any]]
    credential: str
    state_file: Path | None

    @property
    def active(self) -> str:
        """Return the active lane id, or empty for an unreadable chain."""
        return self.chain[0] if self.chain else ""


@dataclass(frozen=True)
class LaneVerdict:
    """What the provenance says about a produced review.

    Attributes:
        status: The red status slug, or empty when the verdict is unchanged.
        reason: One line saying what contradicted the lane, when red.
        warnings: Lines to surface as ``::warning::`` annotations.
    """

    status: str = ""
    reason: str = ""
    warnings: tuple[str, ...] = ()


def lane_facts(env: Mapping[str, str]) -> LaneFacts | None:
    """Read the lane facts the wrapper exported.

    Args:
        env: The environment to read.

    Returns:
        The facts, or ``None`` outside a lane run (nothing exported).
    """
    chain_raw = env.get("LINTRO_REVIEW_CHAIN", "").strip()
    lanes_raw = env.get("LINTRO_REVIEW_LANES", "").strip()
    if not chain_raw and not lanes_raw:
        return None
    chain: tuple[str, ...] = ()
    lanes: dict[str, Mapping[str, Any]] = {}
    try:
        decoded_chain = json.loads(chain_raw or "null")
        decoded_lanes = json.loads(lanes_raw or "null")
    except ValueError:
        decoded_chain, decoded_lanes = None, None
    if isinstance(decoded_chain, list) and all(
        isinstance(entry, str) for entry in decoded_chain
    ):
        chain = tuple(decoded_chain)
    if isinstance(decoded_lanes, dict):
        lanes = {
            str(lane_id): row
            for lane_id, row in decoded_lanes.items()
            if isinstance(row, dict)
        }
    state_dir = env.get("LINTRO_REVIEW_STATE_DIR", "").strip()
    return LaneFacts(
        chain=chain,
        lanes=lanes,
        credential=env.get("LANE_CREDENTIAL", "").strip(),
        state_file=Path(state_dir) / STATE_FILENAME if state_dir else None,
    )


def latest_run_record(state_file: Path | None) -> Mapping[str, Any] | None:
    """Return this round's run record from the persisted state.

    lintro appends the current round's record last (incremental_coverage),
    so the last entry of ``runs`` is this run's.

    Args:
        state_file: The ``state.json`` path, or ``None``.

    Returns:
        The record, or ``None`` when the file, the list or the entry is
        missing or unreadable.
    """
    if state_file is None or not state_file.is_file():
        return None
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        return None
    last = runs[-1]
    return last if isinstance(last, dict) else None


def _host(value: str) -> str:
    """Return the lowercase host a URL or bare host names, or empty."""
    raw = value.strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    except ValueError:
        return ""
    return (parsed.hostname or "").lower()


def _reported(record: Mapping[str, Any]) -> str:
    """Return the models the record says answered, comma-joined."""
    raw = record.get("reported_models")
    if not isinstance(raw, list):
        return ""
    names = [
        str(item.get("canonical") or item.get("model") or "")
        for item in raw
        if isinstance(item, Mapping)
    ]
    return ", ".join(name for name in names if name)


def lane_verdict(
    facts: LaneFacts,
    record: Mapping[str, Any] | None,
) -> LaneVerdict:
    """Apply decision C's rows to a produced review's run record.

    Args:
        facts: The lane facts the wrapper exported.
        record: This round's run record, or ``None`` when none was persisted.

    Returns:
        The verdict: a red status with its reason, or warnings only.
    """
    if record is None:
        return LaneVerdict(
            status=STATUS_PROVENANCE_UNRECORDED,
            reason=(
                "the review persisted no run record, so where it went cannot be "
                "verified"
            ),
        )
    lane = str(record.get("lane") or "")
    row = facts.lanes.get(lane) if lane and lane in facts.chain else None
    if row is None:
        return LaneVerdict(
            status=STATUS_ENDPOINT_MISMATCH,
            reason=(
                f"recorded lane {lane or '(none)'!r} is not in the resolved chain "
                f"{list(facts.chain)}"
            ),
        )
    endpoint = _host(str(record.get("endpoint") or ""))
    expected = _host(str(row.get("endpoint") or ""))
    if endpoint != expected:
        return LaneVerdict(
            status=STATUS_ENDPOINT_MISMATCH,
            reason=(
                f"recorded endpoint {endpoint or '(none)'!r} is not the {lane} "
                f"lane's {expected!r}"
            ),
        )
    warnings: list[str] = []
    if lane != facts.active:
        warnings.append(
            f"fallback lane {lane} answered this round; the active lane "
            f"{facts.active} did not (ADR-0011 D)",
        )
    verification = str(record.get("model_verification") or "")
    reported = _reported(record)
    if bool(row.get("first_party")):
        if verification == "mismatch":
            return LaneVerdict(
                status=STATUS_MODEL_MISMATCH,
                reason=(
                    f"the {lane} lane reported a model outside the one requested "
                    f"({reported or 'unknown'} answered)"
                ),
            )
        if verification == "unreported" and bool(row.get("reports_model_usage")):
            return LaneVerdict(
                status=STATUS_MODEL_UNREPORTED,
                reason=(
                    f"the {lane} lane's transport is declared to report model "
                    "usage and reported none"
                ),
            )
    elif verification == "unverifiable":
        warnings.append(
            f"lane {lane} answered through {endpoint}, a gateway, so the model "
            f"it reports ({reported or 'unknown'}) cannot be verified",
        )
    return LaneVerdict(warnings=tuple(warnings))
