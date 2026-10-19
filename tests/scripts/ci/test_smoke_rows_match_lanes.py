# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Every dogfood lane's real endpoint has a weekly smoke row, or says why not (#2873).

ADR-0011 decision A: adding a lane requires a manifest row, a smoke row and a
credential binding. The two tables are committed separately, so this test is
what holds them together: a lane's ``smoke_row`` must name a row of the
provider-smoke table that calls the same host over the same wire protocol
the lane's CLI speaks, with a credential the API can take. Otherwise a green
smoke would prove nothing about the route the dogfood really uses, which is
how the Z.ai outage went undetected.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlparse

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "scripts" / "ci" / "ai_review_lanes.json"
TABLE = ROOT / "scripts" / "ci" / "ai_provider_smoke" / "providers.json"
RESOLVER = ROOT / "scripts" / "ci" / "resolve_review_lanes.py"

#: The wire protocol each lane provider's CLI speaks to its endpoint; a
#: provider absent here exposes no API protocol lintro implements, so no
#: smoke row can reach it and the lane must declare a gap.
WIRE_PROTOCOL: dict[str, str] = {"anthropic": "anthropic", "openai": "openai"}

#: Credential kinds that are a bearer token the raw API accepts, so the smoke
#: row must use the lane's own secret. A session kind (the Claude and Codex
#: subscriptions) cannot drive the API: its row uses the provider's metered
#: key against the same host, and must not name the session's secret.
TOKEN_KINDS: frozenset[str] = frozenset({"api_key", "zai_token", "kimi_token"})
SESSION_KINDS: frozenset[str] = frozenset({"claude_oauth", "codex_oauth"})


def _load_resolver() -> ModuleType:
    spec = importlib.util.spec_from_file_location("resolve_review_lanes", RESOLVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def resolver() -> ModuleType:
    """Return the manifest resolver."""
    return _load_resolver()


@pytest.fixture(scope="module")
def lanes() -> dict[str, dict[str, Any]]:
    """Return the committed manifest's rows."""
    return dict(json.loads(MANIFEST.read_text(encoding="utf-8"))["lanes"])


@pytest.fixture(scope="module")
def rows() -> dict[str, dict[str, Any]]:
    """Return the committed smoke table's rows by name."""
    table = json.loads(TABLE.read_text(encoding="utf-8"))["providers"]
    return {row["name"]: row for row in table}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def test_every_lane_declares_its_smoke_row_or_a_gap(
    lanes: dict[str, dict[str, Any]],
) -> None:
    """No lane is silent about its weekly smoke."""
    for lane_id, row in lanes.items():
        assert_that(row).described_as(lane_id).contains_key("smoke_row")
        if row["smoke_row"] is None:
            assert_that(row.get("smoke_gap", "")).described_as(lane_id).is_not_empty()
        else:
            assert_that(row).described_as(lane_id).does_not_contain_key("smoke_gap")


def test_a_lane_with_a_smoke_row_is_smoked_on_its_real_route(
    lanes: dict[str, dict[str, Any]],
    rows: dict[str, dict[str, Any]],
) -> None:
    """Same host, same wire protocol, a credential the API can take."""
    for lane_id, lane in lanes.items():
        name = lane["smoke_row"]
        if name is None:
            continue
        assert_that(rows).described_as(f"{lane_id} -> {name}").contains_key(name)
        row = rows[name]
        assert_that(_host(row["base_url"])).described_as(lane_id).is_equal_to(
            _host(lane["endpoint"]),
        )
        assert_that(WIRE_PROTOCOL).described_as(lane_id).contains_key(lane["provider"])
        assert_that(row["protocol"]).described_as(lane_id).is_equal_to(
            WIRE_PROTOCOL[lane["provider"]],
        )
        kind = lane["credential"]
        if kind in TOKEN_KINDS:
            assert_that(row["key_env"]).described_as(lane_id).is_equal_to(
                lane["credential_secret"],
            )
        elif kind in SESSION_KINDS:
            assert_that(row["key_env"]).described_as(lane_id).is_not_equal_to(
                lane["credential_secret"],
            )
        else:
            pytest.fail(f"lane {lane_id!r}: credential kind {kind!r} is not classified")


def test_a_gateway_lane_is_smoked_on_its_own_path_not_the_hosts_other_route(
    lanes: dict[str, dict[str, Any]],
    rows: dict[str, dict[str, Any]],
) -> None:
    """A gateway serves several protocols on one host; the lane's path must match.

    Z.ai's OpenAI route and its Anthropic route share a host and a token and
    go red together, but only the latter is what the claude CLI calls.
    """
    for lane_id, lane in lanes.items():
        if lane["first_party"] or lane["smoke_row"] is None:
            continue
        row = rows[lane["smoke_row"]]
        assert_that(row["base_url"].rstrip("/")).described_as(lane_id).is_equal_to(
            lane["endpoint"].rstrip("/"),
        )
        assert_that(row["model"]).described_as(lane_id).is_equal_to(lane["model"])


def test_a_lane_without_a_smoke_row_has_no_protocol_lintro_could_call(
    lanes: dict[str, dict[str, Any]],
) -> None:
    """The gap is real: only a provider outside the two wire protocols may skip."""
    for lane_id, lane in lanes.items():
        if lane["smoke_row"] is not None:
            continue
        assert_that(WIRE_PROTOCOL).described_as(lane_id).does_not_contain_key(
            lane["provider"],
        )


def test_the_committed_tables_agree_today(
    lanes: dict[str, dict[str, Any]],
    rows: dict[str, dict[str, Any]],
) -> None:
    """The mapping as merged, so a drift names the lane that moved."""
    assert_that(
        {lane_id: lane["smoke_row"] for lane_id, lane in lanes.items()},
    ).is_equal_to(
        {
            "anthropic": "anthropic-api",
            "openai": "openai-api",
            "cursor": None,
            "zai": "zai-anthropic-api",
            "kimi": "kimi-api",
        },
    )
    assert_that(rows["zai-anthropic-api"]["protocol"]).is_equal_to("anthropic")
    assert_that(rows["zai-anthropic-api"]["key_env"]).is_equal_to("ZAI_AUTH_TOKEN")


def _refuses(resolver: ModuleType, document: dict[str, Any], fragment: str) -> None:
    with pytest.raises(resolver.LaneError) as caught:
        resolver.resolve_chain(document)
    assert_that(str(caught.value)).contains(fragment)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda row: row.pop("smoke_row"), "missing 'smoke_row'"),
        (lambda row: row.update(smoke_row=None), "no 'smoke_gap' saying why"),
        (lambda row: row.update(smoke_row=None, smoke_gap="  "), "no 'smoke_gap'"),
        (lambda row: row.update(smoke_row=7), "not a smoke row name"),
        (lambda row: row.update(smoke_row="Not A Row"), "not a smoke row name"),
        (lambda row: row.update(smoke_gap="also"), "cannot be both"),
    ],
)
def test_the_resolver_refuses_a_malformed_declaration(
    resolver: ModuleType,
    mutate: Any,
    fragment: str,
) -> None:
    """A lane cannot be vague about its smoke."""
    document = copy.deepcopy(json.loads(MANIFEST.read_text(encoding="utf-8")))
    mutate(document["lanes"]["kimi"])

    _refuses(resolver, document, fragment)
