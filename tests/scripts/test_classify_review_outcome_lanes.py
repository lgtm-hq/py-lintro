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
#: This workflow run's id and attempt, which the state file must name.
RUN_ID = "38900000001"
RUN_ATTEMPT = "2"


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
            json.dumps(
                {
                    "version": 1,
                    "run_id": RUN_ID,
                    "run_attempt": RUN_ATTEMPT,
                    "runs": [earlier, record],
                },
            ),
            encoding="utf-8",
        )
    return {
        "LINTRO_REVIEW_CHAIN": json.dumps(chain),
        "LINTRO_REVIEW_LANES": json.dumps({lane: rows[lane] for lane in chain}),
        "LANE_CREDENTIAL": credential,
        "LINTRO_REVIEW_STATE_DIR": str(state_dir),
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_RUN_ATTEMPT": RUN_ATTEMPT,
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


def test_a_prior_runs_artifact_is_not_this_runs_provenance(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A downloaded state naming another run cannot vouch for this review.

    The workflow downloads the prior round's artifact into the state
    directory; if this run persisted nothing, that file is still there with
    the prior run's id, and must not be read as this run's record.
    """
    env = _env(tmp_path, rows, ["anthropic"], record=_record())
    state = Path(env["LINTRO_REVIEW_STATE_DIR"]) / "state.json"
    stale = json.loads(state.read_text(encoding="utf-8"))
    stale["run_id"] = "38800000000"
    state.write_text(json.dumps(stale), encoding="utf-8")

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.status).is_equal_to("provenance-unrecorded")
    assert_that(report.headline).contains("belongs to run 38800000000")


def test_an_earlier_attempts_artifact_is_not_this_attempts_provenance(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A rerun resumes its own cancelled attempt's artifact: same run id.

    If this attempt persisted nothing, that file still names the run but an
    earlier attempt, and must not vouch for this attempt's review.
    """
    env = _env(tmp_path, rows, ["anthropic"], record=_record())
    state = Path(env["LINTRO_REVIEW_STATE_DIR"]) / "state.json"
    stale = json.loads(state.read_text(encoding="utf-8"))
    stale["run_attempt"] = "1"
    state.write_text(json.dumps(stale), encoding="utf-8")

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.status).is_equal_to("provenance-unrecorded")
    assert_that(report.headline).contains("belongs to attempt 1 of this run")


@pytest.mark.parametrize(
    ("chain", "record", "fragment"),
    [
        # No verification state at all: nothing says what answered.
        (
            ["anthropic"],
            {"lane": "anthropic", "endpoint": "api.anthropic.com"},
            "'(none)'",
        ),
        # History-only state on a live round.
        (["anthropic"], _record(model_verification="legacy"), "'legacy'"),
        # A gateway record claiming a verification a gateway cannot give.
        (
            ["zai"],
            _record(lane="zai", endpoint="api.z.ai", model_verification="verified"),
            "gateway lane",
        ),
        # A first-party record in a state only a gateway produces.
        (["anthropic"], _record(model_verification="unverifiable"), "first-party lane"),
        # An endpoint-less record.
        (["anthropic"], _record(endpoint=""), "names no endpoint"),
    ],
)
def test_an_incomplete_or_impossible_record_is_red_as_unrecorded(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
    chain: list[str],
    record: dict[str, Any],
    fragment: str,
) -> None:
    """A record must carry a state the lane's row can produce; less is refused."""
    env = _env(tmp_path, rows, chain, record=record)

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.status).is_equal_to("provenance-unrecorded")
    assert_that(report.headline).contains(fragment)


def test_a_lane_in_the_chain_without_a_published_row_is_unrecorded(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """In the chain but with no row to compare against is not a mismatch."""
    env = _env(tmp_path, rows, ["anthropic"], record=_record())
    env["LINTRO_REVIEW_LANES"] = json.dumps({})

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(report.status).is_equal_to("provenance-unrecorded")
    assert_that(report.headline).contains("no manifest row was published")


def test_kill_and_drift_prose_outrank_the_credential_label(
    classifier: ModuleType,
) -> None:
    """A thin envelope that says the runner killed the CLI is not an auth failure."""
    killed = classifier.refine_failure_kind(
        transport="cli",
        kind="",
        message="The operation was canceled; oauth session was open",
        output="",
        credential="zai_token",
    )
    drift = classifier.refine_failure_kind(
        transport="cli",
        kind="unknown",
        message="unknown option --oauth",
        output="",
        credential="zai_token",
    )

    assert_that(killed).is_equal_to("killed_externally")
    assert_that(drift).is_equal_to("cli_version_drift")


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


def test_thin_auth_prose_is_labelled_by_the_bound_credential_too(
    classifier: ModuleType,
) -> None:
    """A thin envelope whose prose says OAuth still names the bound credential."""
    label = classifier.refine_failure_kind(
        transport="cli",
        kind="",
        message="not logged in, run /login",
        output="",
        credential="codex_oauth",
    )

    assert_that(label).is_equal_to("auth_failed:codex_session")


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


def test_the_reader_matches_the_state_lintro_really_writes(tmp_path: Path) -> None:
    """The producer's ``state.json`` carries ``run_id`` and the record where read.

    ``write_state_part(final=True)`` is what the review's final persist calls;
    the verdict module must find this run's id at the top level and this
    round's record as the last run, or every CI run would read as unrecorded.
    """
    from lintro.ai.review.models.review_state import ReviewState
    from lintro.ai.review.models.run_identity import RunIdentity
    from lintro.ai.review.models.run_record import RunRecord
    from lintro.ai.review.state_store import write_state_part

    verdict = importlib.util.spec_from_file_location(
        "review_lane_verdict",
        SCRIPT.parent / "review_lane_verdict.py",
    )
    assert verdict is not None and verdict.loader is not None
    module = importlib.util.module_from_spec(verdict)
    verdict.loader.exec_module(module)
    earlier = RunRecord(identity=RunIdentity(round=1, lane="anthropic"))
    current = RunRecord(
        identity=RunIdentity(
            round=2,
            lane="anthropic",
            endpoint="api.anthropic.com",
            first_party=True,
            credential="claude_oauth",
            model_verification="verified",
        ),
    )
    state_dir = tmp_path / "ai-review-state"
    write_state_part(
        state=ReviewState(
            runs=(earlier, current),
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
        ),
        directory=state_dir,
        sequence=1,
        final=True,
    )
    state = state_dir / "state.json"

    record, missing = module.latest_run_record(
        state,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
    )

    assert_that(missing).is_empty()
    assert record is not None
    assert_that(record["round"]).is_equal_to(2)
    assert_that(record["lane"]).is_equal_to("anthropic")
    assert_that(record["model_verification"]).is_equal_to("verified")
    stale, why = module.latest_run_record(
        state,
        run_id="other",
        run_attempt=RUN_ATTEMPT,
    )
    assert_that(stale).is_none()
    assert_that(why).contains(f"belongs to run {RUN_ID}")
    earlier_attempt, why = module.latest_run_record(
        state,
        run_id=RUN_ID,
        run_attempt="3",
    )
    assert_that(earlier_attempt).is_none()
    assert_that(why).contains(f"belongs to attempt {RUN_ATTEMPT} of this run")


def test_record_strings_are_clamped_before_they_reach_an_annotation(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A hostile or corrupt record cannot flood the annotation or summary."""
    flood = "x" * 200_000
    env = _env(tmp_path, rows, ["anthropic"], record=_record(lane=flood))

    report = classifier.classify(status=0, output="{}", env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.CONTRADICTED)
    assert_that(len(report.headline)).is_less_than(600)
    assert_that(report.headline).contains("…")


def test_a_partial_review_keeps_its_own_reason_when_provenance_is_missing(
    classifier: ModuleType,
    rows: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A budget stop before any call is red for the budget, with a warning.

    Such a round has no provider call to record, so its record carries no
    verification state; the actionable reason (raise the budget) must not
    be buried under ``provenance-unrecorded``.
    """
    env = _env(
        tmp_path,
        rows,
        ["anthropic"],
        record=_record(model_verification="", reported_models=[]),
    )
    envelope = json.dumps(
        {
            "readiness_verdict": "incomplete",
            "coverage": {"complete": False, "covered_at_head": 0, "eligible": 4},
            "stopped_reason": "PR budget of $1.00 reached",
            "findings": [],
        },
    )

    report = classifier.classify(status=0, output=envelope, env=env)

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.PR_BUDGET)
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.status).is_empty()
    assert_that(report.warnings).is_length(1)
    assert_that(report.warnings[0]).contains("provenance not verified")


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
