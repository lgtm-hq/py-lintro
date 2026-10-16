# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""A produced review that contradicts its lane is red (ADR-0011 C, #2871).

The classifier reads the lane facts the wrapper exported and this round's
run record from ``state.json``; each row of decision C's verdict table has a
test here, as does the auth-failure label by credential kind.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "classify_review_outcome.py"
MANIFEST = REPO_ROOT / "scripts" / "ci" / "ai_review_lanes.json"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("classify_review_outcome", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["classify_review_outcome"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def classifier() -> ModuleType:
    """Return the loaded classifier module."""
    return _load()


@pytest.fixture(scope="module")
def rows() -> dict[str, Any]:
    """Return the committed manifest's rows."""
    return dict(json.loads(MANIFEST.read_text(encoding="utf-8"))["lanes"])


def _env(
    tmp_path: Path,
    rows: dict[str, Any],
    chain: list[str],
    *,
    record: dict[str, Any] | None,
    credential: str = "claude_oauth",
) -> dict[str, str]:
    """Build the environment the wrapper exports, with a state file.

    Args:
        tmp_path: Where ``state.json`` is written.
        rows: The manifest rows.
        chain: The resolved chain.
        record: This round's run record, or ``None`` for no state file.
        credential: The credential kind the active lane bound.

    Returns:
        The environment mapping.
    """
    state_dir = tmp_path / "ai-review-state"
    state_dir.mkdir(exist_ok=True)
    if record is not None:
        earlier = {"round": 1, "lane": "anthropic", "endpoint": "api.anthropic.com"}
        (state_dir / "state.json").write_text(
            json.dumps({"version": 1, "runs": [earlier, record]}),
            encoding="utf-8",
        )
    return {
        "LINTRO_REVIEW_CHAIN": json.dumps(chain),
        "LINTRO_REVIEW_LANES": json.dumps({lane: rows[lane] for lane in chain}),
        "LANE_CREDENTIAL": credential,
        "LINTRO_REVIEW_STATE_DIR": str(state_dir),
    }


def _record(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "round": 2,
        "lane": "anthropic",
        "endpoint": "api.anthropic.com",
        "first_party": True,
        "credential": "claude_oauth",
        "model_verification": "verified",
        "reported_models": [{"model": "claude-opus-5-5", "provider": "anthropic"}],
    }
    base.update(fields)
    return base


# --- the verdict table ---------------------------------------------------------


def test_a_verified_first_party_round_is_unchanged(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """``verified`` on the active lane leaves the outcome and exit code alone."""
    env = _env(tmp_path, rows, ["anthropic"], record=_record())

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.status).is_empty()
    assert_that(report.warnings).is_empty()


def test_a_lane_outside_the_chain_is_an_endpoint_mismatch(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A record naming a lane the chain does not carry can never be green."""
    env = _env(tmp_path, rows, ["anthropic"], record=_record(lane="zai"))

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.status).is_equal_to("endpoint-mismatch")
    assert_that(report.headline).contains("status=endpoint-mismatch")
    assert_that(report.headline).contains("'zai'")


def test_an_endpoint_off_the_lanes_host_is_an_endpoint_mismatch(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The incident shape: the anthropic lane answered from api.z.ai."""
    env = _env(
        tmp_path,
        rows,
        ["anthropic"],
        record=_record(endpoint="https://api.z.ai/api/anthropic", first_party=False),
    )

    report = classifier.classify(status=1, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.status).is_equal_to("endpoint-mismatch")
    assert_that(report.headline).contains("'api.z.ai'")
    assert_that(report.headline).contains("'api.anthropic.com'")
    # The review that was produced is still named, so a reader knows what
    # to distrust.
    assert_that(report.detail).contains("P1 findings")


def test_a_fallback_lane_warns_but_keeps_the_verdict(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A lane in the chain that is not chain[0] answered: warning, not red."""
    env = _env(
        tmp_path,
        rows,
        ["anthropic", "openai"],
        record=_record(
            lane="openai",
            endpoint="api.openai.com",
            credential="codex_oauth",
            model_verification="unreported",
        ),
    )

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.warnings).is_length(1)
    assert_that(report.warnings[0]).contains("fallback lane openai")
    assert_that(report.warnings[0]).contains("active lane anthropic")


def test_a_model_mismatch_on_a_first_party_lane_is_red(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A first-party lane that answered with another model cannot be green."""
    env = _env(
        tmp_path,
        rows,
        ["anthropic"],
        record=_record(
            model_verification="mismatch",
            reported_models=[
                {"model": "claude-haiku-4-5", "canonical": "claude-haiku-4-5"},
            ],
        ),
    )

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.status).is_equal_to("model-mismatch")
    assert_that(report.headline).contains("claude-haiku-4-5")


def test_unreported_on_a_lane_that_declares_model_usage_is_red(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The anthropic row declares ``reports_model_usage``; silence is a defect."""
    env = _env(
        tmp_path,
        rows,
        ["anthropic"],
        record=_record(model_verification="unreported", reported_models=[]),
    )

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.status).is_equal_to("model-unreported")


def test_unreported_on_a_lane_that_declares_none_is_unchanged(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The cursor row declares ``reports_model_usage: false``: unchanged."""
    env = _env(
        tmp_path,
        rows,
        ["cursor"],
        record=_record(
            lane="cursor",
            endpoint="api2.cursor.sh",
            credential="cursor_api_key",
            model_verification="unreported",
            reported_models=[],
        ),
        credential="cursor_api_key",
    )

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.status).is_empty()
    assert_that(report.warnings).is_empty()


def test_unverifiable_on_a_gateway_lane_warns_naming_lane_and_endpoint(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A declared gateway may answer unverifiably; it is disclosed, not hidden."""
    env = _env(
        tmp_path,
        rows,
        ["zai"],
        record=_record(
            lane="zai",
            endpoint="api.z.ai",
            first_party=False,
            credential="zai_token",
            model_verification="unverifiable",
            reported_models=[{"model": "glm-5.3-flash", "provider": "zai"}],
        ),
        credential="zai_token",
    )

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.warnings).is_length(1)
    assert_that(report.warnings[0]).contains("lane zai")
    assert_that(report.warnings[0]).contains("api.z.ai")
    assert_that(report.warnings[0]).contains("glm-5.3-flash")


def test_a_missing_run_record_is_red_as_unrecorded(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A produced review whose provenance was never persisted cannot be verified."""
    env = _env(tmp_path, rows, ["anthropic"], record=None)

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.status).is_equal_to("provenance-unrecorded")


def test_an_unreadable_chain_trusts_no_recorded_lane(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Exported lane facts that do not parse refuse rather than skip."""
    env = _env(tmp_path, rows, ["anthropic"], record=_record())
    env["LINTRO_REVIEW_CHAIN"] = "not json"

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.status).is_equal_to("endpoint-mismatch")


def test_outside_a_lane_run_nothing_is_checked(
    classifier: ModuleType,
) -> None:
    """A developer shell exports no lane facts, so the outcome is as before."""
    report = classifier.classify(status=0, output="{}", env={})

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.status).is_empty()


def test_a_failed_review_is_not_second_guessed_by_the_lane(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Only a produced review has provenance to contradict."""
    env = _env(tmp_path, rows, ["anthropic"], record=None)
    envelope = json.dumps(
        {
            "error": {
                "kind": "insufficient_credits",
                "provider": "anthropic",
                "status": 400,
                "retryable": False,
                "provider_unavailable": True,
                "message": "balance too low",
            },
        },
    )

    report = classifier.classify(status=2, output=envelope, env=env)

    assert_that(report.outcome).is_equal_to(
        classifier.ReviewOutcome.PROVIDER_UNAVAILABLE,
    )
    assert_that(report.status).is_empty()


# --- the auth-failure label ------------------------------------------------------


@pytest.mark.parametrize(
    ("credential", "label"),
    [
        ("claude_oauth", "auth_failed:oauth_session"),
        ("codex_oauth", "auth_failed:codex_session"),
        ("cursor_api_key", "auth_failed:key"),
        ("zai_token", "auth_failed:gateway_token"),
        ("kimi_token", "auth_failed:gateway_token"),
    ],
)
def test_an_auth_failure_is_labelled_by_the_bound_credential(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
    credential: str,
    label: str,
) -> None:
    """A gateway 401 is never blamed on the Claude session (ADR-0011 C)."""
    env = _env(tmp_path, rows, ["anthropic"], record=None, credential=credential)
    envelope = json.dumps(
        {
            "error": {
                "kind": "auth_failed",
                "provider": "anthropic",
                "status": 401,
                "retryable": False,
                "provider_unavailable": True,
                "message": "not logged in, run /login",
            },
        },
    )

    report = classifier.classify(status=2, output=envelope, env=env)

    assert_that(report.outcome).is_equal_to(
        classifier.ReviewOutcome.PROVIDER_UNAVAILABLE,
    )
    assert_that(report.headline).contains(f"({label})")


def test_without_a_bound_credential_the_prose_heuristics_still_apply(
    classifier: ModuleType,
) -> None:
    """Outside a lane run the old session-versus-key heuristic decides."""
    label = classifier.refine_failure_kind(
        transport="cli",
        kind="auth_failed",
        message="invalid api key",
        output="",
    )

    assert_that(label).is_equal_to("auth_failed:key")


# --- surfaces --------------------------------------------------------------------


def test_warnings_and_status_reach_the_annotation_and_summary(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback warning is a ``::warning::``; a red verdict is an ``::error::``."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    env = _env(
        tmp_path,
        rows,
        ["anthropic", "openai"],
        record=_record(
            lane="openai",
            endpoint="api.openai.com",
            model_verification="unreported",
        ),
    )
    warned = classifier.classify(status=0, output="{}", env=env)
    classifier._emit(report=warned)
    out = capsys.readouterr().out
    assert_that(out).contains("::notice title=AI Review (cli)::")
    assert_that(out).contains("::warning title=AI Review (cli)::fallback lane openai")
    assert_that(summary.read_text(encoding="utf-8")).contains("⚠️ fallback lane openai")

    red = classifier.classify(
        status=0,
        output="{}",
        env=_env(tmp_path, rows, ["anthropic"], record=_record(lane="kimi")),
    )
    classifier._emit(report=red)
    out = capsys.readouterr().out
    assert_that(out).contains("::error title=AI Review (cli)::")
    assert_that(out).contains("status=endpoint-mismatch")
    text = summary.read_text(encoding="utf-8")
    assert_that(text).contains("🚫 AI Review (cli)")
    assert_that(text).contains("treat the posted review as unverified")


def test_the_lane_module_is_stdlib_only() -> None:
    """The verdict module runs from a bare python3 like the classifier."""
    source = (SCRIPT.parent / "review_lane_verdict.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if line.startswith(("import ", "from ")):
            assert_that(line).does_not_contain("lintro")


def test_the_status_slugs_match_the_adr() -> None:
    """The slugs the ADR's verdict table names are the ones emitted."""
    adr = (
        REPO_ROOT
        / "docs"
        / "adr"
        / "0011-dogfood-review-lanes-provenance-and-degradation.md"
    ).read_text(encoding="utf-8")
    for slug in ("endpoint-mismatch", "model-mismatch", "model-unreported"):
        assert_that(adr).contains(f"`status={slug}`")
