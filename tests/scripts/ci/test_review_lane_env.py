# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""The wrapper binds one attempt to its resolved lane (ADR-0011 A and D, #2870).

``review_lane_env.py`` turns the lane job's outputs into the attempt's
exports and refuses, before any provider call, an absent credential, a
competing one in the environment or on disk, a base-URL override that names
another host, and an effective config that disagrees with the row.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess  # nosec B404 - runs the repo's own script with a fixed argv
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "review_lane_env.py"
MANIFEST = ROOT / "scripts" / "ci" / "ai_review_lanes.json"
#: A stand-in credential value, assembled so no credential-shaped literal is
#: committed and bandit does not read a keyword as a password.
_TOKEN = "dummy-" + "token"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("review_lane_env", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    """Return the gate module."""
    return _load()


@pytest.fixture
def lanes() -> dict[str, Any]:
    """Return the committed manifest's rows."""
    return copy.deepcopy(json.loads(MANIFEST.read_text(encoding="utf-8"))["lanes"])


def _env(
    lanes: dict[str, Any],
    chain: list[str],
    home: Path,
    **extra: str,
) -> dict[str, str]:
    rows = {lane_id: lanes[lane_id] for lane_id in chain}
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "LINTRO_REVIEW_CHAIN": json.dumps(chain),
        "LINTRO_REVIEW_LANES": json.dumps(rows),
        **extra,
    }


def _run(phase: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 - fixed argv, the repo's own script
        [sys.executable, "-I", str(SCRIPT), "--phase", phase],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _exports(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if line)


# --- the env phase ---------------------------------------------------------------


def test_the_env_phase_exports_the_rows_facts_and_overlays(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Provider, transport, model and the run-record facts come from the row."""
    result = _run("env", _env(lanes, ["claude-subscription"], tmp_path))

    assert_that(result.returncode).is_equal_to(0)
    exports = _exports(result.stdout)
    assert_that(exports["LINTRO_REVIEW_LANE"]).is_equal_to("claude-subscription")
    assert_that(exports["LINTRO_REVIEW_ENDPOINT"]).is_equal_to(
        "https://api.anthropic.com",
    )
    assert_that(exports["LINTRO_REVIEW_FIRST_PARTY"]).is_equal_to("true")
    assert_that(exports["LINTRO_REVIEW_ALLOWED_MODELS"]).is_equal_to(
        "claude-opus-5-5,claude-sonnet-5-5",
    )
    assert_that(exports["LINTRO_REVIEW_REPORTS_MODEL_USAGE"]).is_equal_to("true")
    assert_that(exports["LINTRO_AI_PROVIDER"]).is_equal_to("anthropic")
    assert_that(exports["LINTRO_AI_TRANSPORT"]).is_equal_to("cli")
    assert_that(exports["LINTRO_AI_MODEL"]).is_equal_to("claude-opus-5-5")
    assert_that(exports["LANE_CREDENTIAL_SECRET"]).is_equal_to(
        "CLAUDE_CODE_OAUTH_TOKEN",
    )
    assert_that(exports["LANE_CREDENTIAL_ENV"]).is_equal_to("CLAUDE_CODE_OAUTH_TOKEN")
    # First-party: the override is left unset.
    assert_that(exports["LANE_BASE_URL_ENV"]).is_equal_to("ANTHROPIC_BASE_URL")
    assert_that(exports["LANE_BASE_URL_VALUE"]).is_empty()
    for value in exports.values():
        assert_that(value).does_not_contain("\n")


def test_a_gateway_lane_exports_its_endpoint_as_the_override(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Off-host the base-URL variable is set to the row's endpoint, by the wrapper."""
    exports = _exports(_run("env", _env(lanes, ["zai-glm-gateway"], tmp_path)).stdout)

    assert_that(exports["LANE_BASE_URL_VALUE"]).is_equal_to(
        "https://api.z.ai/api/anthropic",
    )
    assert_that(exports["LANE_CREDENTIAL_SECRET"]).is_equal_to("ZAI_AUTH_TOKEN")
    assert_that(exports["LANE_CREDENTIAL_ENV"]).is_equal_to("ANTHROPIC_AUTH_TOKEN")
    assert_that(exports["LINTRO_REVIEW_FIRST_PARTY"]).is_equal_to("false")


def test_the_attempt_index_selects_the_lane_and_is_bounded(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Attempt 1 of a two-lane chain binds the second lane; past the end refuses."""
    chain = ["claude-subscription", "codex-subscription"]
    second = _run("env", _env(lanes, chain, tmp_path, LINTRO_REVIEW_ATTEMPT="1"))
    assert_that(_exports(second.stdout)["LINTRO_REVIEW_LANE"]).is_equal_to(
        "codex-subscription",
    )
    beyond = _run("env", _env(lanes, chain, tmp_path, LINTRO_REVIEW_ATTEMPT="2"))
    assert_that(beyond.returncode).is_equal_to(3)
    assert_that(beyond.stderr).contains("::error::review lane:")


def test_a_row_whose_name_fields_are_not_names_is_refused(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The wrapper looks variables up by these names; the gate validates them."""
    for field in ("credential_secret", "credential_env", "base_url_env"):
        hostile = copy.deepcopy(lanes)
        hostile["claude-subscription"][
            field
        ] = 'CLAUDE_CODE_OAUTH_TOKEN}"; touch /tmp/x; : "${X'
        for phase in ("env", "check"):
            result = _run(phase, _env(hostile, ["claude-subscription"], tmp_path))
            assert_that(result.returncode).described_as(f"{field}/{phase}").is_equal_to(
                3,
            )
            assert_that(result.stderr).contains("is not a variable name")
            assert_that(result.stdout).is_empty()


def test_missing_or_malformed_lane_outputs_refuse(tmp_path: Path) -> None:
    """Without the lane job's outputs there is nothing to bind to."""
    result = _run("env", {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert_that(result.returncode).is_equal_to(3)
    result = _run(
        "env",
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "LINTRO_REVIEW_CHAIN": '["x"]',
            "LINTRO_REVIEW_LANES": "{}",
        },
    )
    assert_that(result.returncode).is_equal_to(3)
    assert_that(result.stderr).contains("no published row")


# --- the check phase -------------------------------------------------------------


def test_an_absent_credential_is_a_no_credential_refusal(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Exit 2 is the wrapper's cue for status=no-credential, never a fallback."""
    result = _run("check", _env(lanes, ["claude-subscription"], tmp_path))

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("no provider credential")
    assert_that(result.stderr).contains("CLAUDE_CODE_OAUTH_TOKEN")


def test_the_bound_credential_passes_and_a_competitor_refuses(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Exactly one credential may be in scope: the lane's, under its CLI variable."""
    ok = _run(
        "check",
        _env(lanes, ["claude-subscription"], tmp_path, CLAUDE_CODE_OAUTH_TOKEN=_TOKEN),
    )
    assert_that(ok.returncode).is_equal_to(0)
    assert_that(ok.stderr).contains("no competitor")

    competing = _run(
        "check",
        _env(
            lanes,
            ["claude-subscription"],
            tmp_path,
            CLAUDE_CODE_OAUTH_TOKEN=_TOKEN,
            CURSOR_API_KEY="key",
        ),
    )
    assert_that(competing.returncode).is_equal_to(3)
    assert_that(competing.stderr).contains("competing credentials ['CURSOR_API_KEY']")


def test_a_session_on_disk_competes_with_a_non_codex_lane(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The on-disk check covers the codex session file."""
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "auth.json").write_text("{}", encoding="utf-8")

    claude = _run(
        "check",
        _env(lanes, ["claude-subscription"], tmp_path, CLAUDE_CODE_OAUTH_TOKEN=_TOKEN),
    )
    assert_that(claude.returncode).is_equal_to(3)
    assert_that(claude.stderr).contains("codex_oauth session")

    # The codex lane itself needs its source variable, and owns the file.
    codex = _run(
        "check",
        _env(lanes, ["codex-subscription"], tmp_path, CODEX_AUTH_JSON="e30="),
    )
    assert_that(codex.returncode).is_equal_to(0)


def test_the_base_url_override_must_name_the_lane_endpoint(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Set on a first-party lane, or naming another host off-host, is a refusal."""
    on_host = _run(
        "check",
        _env(
            lanes,
            ["claude-subscription"],
            tmp_path,
            CLAUDE_CODE_OAUTH_TOKEN=_TOKEN,
            ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic",
        ),
    )
    assert_that(on_host.returncode).is_equal_to(3)
    assert_that(on_host.stderr).contains("must be unset")

    elsewhere = _run(
        "check",
        _env(
            lanes,
            ["zai-glm-gateway"],
            tmp_path,
            ANTHROPIC_AUTH_TOKEN=_TOKEN,
            ANTHROPIC_BASE_URL="https://proxy.example/anthropic",
        ),
    )
    assert_that(elsewhere.returncode).is_equal_to(3)
    assert_that(elsewhere.stderr).contains("not the lane endpoint")

    matching = _run(
        "check",
        _env(
            lanes,
            ["zai-glm-gateway"],
            tmp_path,
            ANTHROPIC_AUTH_TOKEN=_TOKEN,
            ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic",
        ),
    )
    assert_that(matching.returncode).is_equal_to(0)


def test_a_file_backed_override_is_checked_too(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Codex's config.toml may not point the CLI elsewhere."""
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        'chatgpt_base_url = "https://evil.example/backend"\n',
        encoding="utf-8",
    )

    result = _run(
        "check",
        _env(lanes, ["codex-subscription"], tmp_path, CODEX_AUTH_JSON="e30="),
    )

    assert_that(result.returncode).is_equal_to(3)
    assert_that(result.stderr).contains("chatgpt_base_url")


def test_a_row_that_names_no_model_allows_no_override_and_no_fallback(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """``model: null`` means the CLI's own default; a config may not name one."""
    base = _env(lanes, ["codex-subscription"], tmp_path, CODEX_AUTH_JSON="e30=")
    agreeing = {"provider": "openai", "transport": "cli", "model": None}
    assert_that(
        _run(
            "check",
            {**base, "LINTRO_EFFECTIVE_AI_JSON": json.dumps(agreeing)},
        ).returncode,
    ).is_equal_to(0)
    named = _run(
        "check",
        {
            **base,
            "LINTRO_EFFECTIVE_AI_JSON": json.dumps({**agreeing, "model": "gpt-5"}),
        },
    )
    assert_that(named.returncode).is_equal_to(3)
    assert_that(named.stderr).contains("effective ai.model is 'gpt-5'")
    fallback = _run(
        "check",
        {
            **base,
            "LINTRO_EFFECTIVE_AI_JSON": json.dumps(
                {**agreeing, "fallback_models": ["gpt-5"]},
            ),
        },
    )
    assert_that(fallback.returncode).is_equal_to(3)
    assert_that(fallback.stderr).contains("fallback_models ['gpt-5']")


def test_a_file_backed_override_is_parsed_as_toml_and_fails_closed(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Single-quoted TOML is TOML; an unparsable file refuses the attempt."""
    (tmp_path / ".codex").mkdir()
    config = tmp_path / ".codex" / "config.toml"
    env = _env(lanes, ["codex-subscription"], tmp_path, CODEX_AUTH_JSON="e30=")

    config.write_text(
        "chatgpt_base_url = 'https://evil.example/backend'\n",
        encoding="utf-8",
    )
    assert_that(_run("check", env).returncode).is_equal_to(3)

    config.write_text("chatgpt_base_url = [\n", encoding="utf-8")
    broken = _run("check", env)
    assert_that(broken.returncode).is_equal_to(3)
    assert_that(broken.stderr).contains("could not be read as TOML")

    config.write_text('model = "gpt-5.3-codex"\n', encoding="utf-8")
    assert_that(_run("check", env).returncode).is_equal_to(0)


def test_the_effective_config_must_agree_with_the_row(
    lanes: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The committed config and its overlays are not a second routing source."""
    base = _env(
        lanes,
        ["claude-subscription"],
        tmp_path,
        CLAUDE_CODE_OAUTH_TOKEN=_TOKEN,
    )
    agreeing = {
        "provider": "anthropic",
        "transport": "cli",
        "model": "claude-opus-5-5",
        "api_base_url": None,
        "api_key_env": None,
        "fallback_models": ["claude-sonnet-5-5"],
    }
    ok = _run("check", {**base, "LINTRO_EFFECTIVE_AI_JSON": json.dumps(agreeing)})
    assert_that(ok.returncode).is_equal_to(0)

    for field, value, fragment in (
        ("provider", "openai", "effective ai.provider"),
        ("api_base_url", "https://proxy.example", "ai.api_base_url"),
        ("api_key_env", "MY_KEY", "ai.api_key_env"),
        ("fallback_models", ["gpt-4o"], "fallback_models ['gpt-4o']"),
    ):
        disagreeing = {**agreeing, field: value}
        result = _run(
            "check",
            {**base, "LINTRO_EFFECTIVE_AI_JSON": json.dumps(disagreeing)},
        )
        assert_that(result.returncode).described_as(field).is_equal_to(3)
        assert_that(result.stderr).described_as(field).contains(fragment)

    unreadable = _run(
        "check",
        {**base, "LINTRO_EFFECTIVE_AI_JSON": json.dumps({"error": "boom"})},
    )
    assert_that(unreadable.returncode).is_equal_to(3)


def test_the_gate_is_stdlib_only(gate: ModuleType) -> None:
    """The refusal path must not depend on a synced virtualenv."""
    text = SCRIPT.read_text(encoding="utf-8")
    for module in ("lintro", "yaml", "pydantic", "click"):
        assert_that(text).does_not_contain(f"import {module}")
        assert_that(text).does_not_contain(f"from {module}")
    assert_that(gate.EXIT_NO_CREDENTIAL).is_equal_to(2)
    assert_that(gate.EXIT_REFUSED).is_equal_to(3)
