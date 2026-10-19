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
#: row must send the lane's own secret and the lane declares ``smoke_covers:
#: route``. A session kind (the Claude and Codex subscriptions) cannot drive
#: the API: its row sends the provider's metered key, and the lane declares
#: how far that reaches (``SESSION_COVERS``), saying out loud that the
#: session itself is never probed.
TOKEN_KINDS: frozenset[str] = frozenset({"api_key", "zai_token", "kimi_token"})
SESSION_KINDS: frozenset[str] = frozenset({"claude_oauth", "codex_oauth"})

#: The metered key a session lane's smoke row must send: the repository
#: secret the provider's raw API takes, pinned so that no other secret (a
#: session, another provider's key) can stand in for it.
METERED_KEY: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

#: What a session lane's row can honestly claim. The claude CLI on a
#: subscription calls api.anthropic.com/v1/messages, the same host and path
#: the metered key reaches: ``host``. The Codex CLI on a ChatGPT session
#: calls the ChatGPT backend (chatgpt.com, in the lane's egress), which no
#: API smoke can reach, so openai-api proves only the provider's API answers:
#: ``provider``. A lane may not claim more than this table allows.
SESSION_COVERS: dict[str, str] = {"anthropic": "host", "openai": "provider"}


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


def _check_lane(
    lane_id: str,
    lane: dict[str, Any],
    rows: dict[str, dict[str, Any]],
) -> None:
    """Assert one lane's smoke row stands for it with a usable credential.

    Args:
        lane_id: The lane's id, for the failure message.
        lane: The manifest row.
        rows: The smoke table's rows by name.
    """
    name = lane["smoke_row"]
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
        assert_that(lane["smoke_covers"]).described_as(lane_id).is_equal_to("route")
        assert_that(row["key_env"]).described_as(lane_id).is_equal_to(
            lane["credential_secret"],
        )
    elif kind in SESSION_KINDS:
        assert_that(SESSION_COVERS).described_as(lane_id).contains_key(lane["provider"])
        assert_that(lane["smoke_covers"]).described_as(lane_id).is_equal_to(
            SESSION_COVERS[lane["provider"]],
        )
        assert_that(METERED_KEY).described_as(lane_id).contains_key(lane["provider"])
        assert_that(row["key_env"]).described_as(lane_id).is_equal_to(
            METERED_KEY[lane["provider"]],
        )
    else:
        pytest.fail(f"lane {lane_id!r}: credential kind {kind!r} is not classified")


def test_a_lane_with_a_smoke_row_is_smoked_as_far_as_it_claims(
    lanes: dict[str, dict[str, Any]],
    rows: dict[str, dict[str, Any]],
) -> None:
    """Same host, same wire protocol, the credential and level the lane declares."""
    for lane_id, lane in lanes.items():
        if lane["smoke_row"] is None:
            continue
        _check_lane(lane_id, lane, rows)


@pytest.mark.parametrize(
    ("lane_id", "key_env"),
    [
        # A session secret cannot drive the raw API: not "any other secret".
        ("anthropic", "CLAUDE_CODE_OAUTH_TOKEN"),
        ("openai", "CODEX_AUTH_JSON"),
        # Another provider's key is not this host's key.
        ("anthropic", "OPENAI_API_KEY"),
        ("openai", "ANTHROPIC_API_KEY"),
        ("anthropic", "ZAI_AUTH_TOKEN"),
        # A token lane's row must send that lane's own token.
        ("kimi", "ZAI_AUTH_TOKEN"),
        ("zai", "ANTHROPIC_API_KEY"),
    ],
)
def test_a_row_sending_the_wrong_secret_is_caught(
    lanes: dict[str, dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    lane_id: str,
    key_env: str,
) -> None:
    """The credential check pins the key, so a usable-looking swap still fails."""
    lane = lanes[lane_id]
    swapped = copy.deepcopy(rows)
    swapped[lane["smoke_row"]]["key_env"] = key_env

    with pytest.raises(AssertionError):
        _check_lane(lane_id, lane, swapped)


@pytest.mark.parametrize(
    ("lane_id", "covers"),
    [
        # A session lane may not claim its session is probed.
        ("anthropic", "route"),
        ("openai", "route"),
        # The Codex session's route is not the API host: not even "host".
        ("openai", "host"),
        # A token lane may not hide behind a weaker level.
        ("kimi", "host"),
        ("zai", "provider"),
    ],
)
def test_a_lane_cannot_overstate_or_understate_its_coverage(
    lanes: dict[str, dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    lane_id: str,
    covers: str,
) -> None:
    """The declared level is pinned per lane, in both directions."""
    lane = dict(lanes[lane_id], smoke_covers=covers)

    with pytest.raises(AssertionError):
        _check_lane(lane_id, lane, rows)


def test_a_route_level_row_is_the_lanes_own_path_not_the_hosts_other_route(
    lanes: dict[str, dict[str, Any]],
    rows: dict[str, dict[str, Any]],
) -> None:
    """``route`` means the lane's own path and model, not just its host.

    A gateway serves several protocols on one host: Z.ai's OpenAI route and
    its Anthropic route share a host and a token and go red together, but
    only the latter is what the claude CLI calls. Keyed on the declared level,
    so a future first-party token lane claiming ``route`` is held to it too.
    """
    for lane_id, lane in lanes.items():
        if lane.get("smoke_covers") != "route":
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
        {
            lane_id: (lane["smoke_row"], lane.get("smoke_covers"))
            for lane_id, lane in lanes.items()
        },
    ).is_equal_to(
        {
            "anthropic": ("anthropic-api", "host"),
            "openai": ("openai-api", "provider"),
            "cursor": (None, None),
            "zai": ("zai-anthropic-api", "route"),
            "kimi": ("kimi-api", "route"),
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
        (lambda row: row.update(smoke_row=7), "has the wrong type"),
        (lambda row: row.update(smoke_row="Not A Row"), "not a smoke row name"),
        # ``$`` alone would accept a trailing line break; fullmatch does not.
        (lambda row: row.update(smoke_row="kimi-api\n"), "not a smoke row name"),
        (lambda row: row.update(smoke_gap="also"), "cannot be both"),
        (lambda row: row.pop("smoke_covers"), "'smoke_covers' must be one of"),
        (lambda row: row.update(smoke_covers="all"), "'smoke_covers' must be one of"),
        (
            lambda row: row.update(
                smoke_row=None,
                smoke_gap="why",
                smoke_covers="host",
            ),
            "no smoke row but a 'smoke_covers'",
        ),
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
