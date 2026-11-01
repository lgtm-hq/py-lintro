"""Carried coverage keeps its actual evidence through finalization and CI (#3102)."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.reported_model import ReportedModel
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.coverage_rounds import hashes_for_diffs
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.output import review_result_to_json
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.state_store import write_state_part
from lintro.ai.review.sticky.assembly import advance_review_state

_MODEL = "gpt-6.1-sol"
_REPORTED = ReportedModel(model=_MODEL, canonical=_MODEL, provider="openai")
_ROW = {
    "endpoint": "https://api.openai.com",
    "first_party": True,
    "reports_model_usage": False,
}


@pytest.fixture
def lane_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """Use an explicit lane without consulting local provider settings."""
    env = {
        "LINTRO_REVIEW_LANE": "openai",
        "LINTRO_REVIEW_ENDPOINT": "api.openai.com",
        "LINTRO_REVIEW_FIRST_PARTY": "true",
        "LINTRO_REVIEW_REPORTS_MODEL_USAGE": "false",
        "LINTRO_REVIEW_ALLOWED_MODELS": _MODEL,
        "OPENAI_BASE_URL": "https://api.openai.com",
        "LINTRO_REVIEW_CHAIN": '["openai"]',
        "LINTRO_REVIEW_LANES": json.dumps({"openai": _ROW}),
        "LINTRO_REVIEW_STATE_DIR": str(tmp_path),
        "GITHUB_RUN_ID": "3102",
        "GITHUB_RUN_ATTEMPT": "2",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


@pytest.fixture(scope="module")
def classifier() -> ModuleType:
    """Load the real standard-library CI classifier."""
    script = (
        Path(__file__).resolve().parents[2] / "scripts/ci/classify_review_outcome.py"
    )
    spec = importlib.util.spec_from_file_location("classify_review_outcome", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _context() -> ReviewContext:
    paths = ("a.py", "b.py", "copy.py")
    # copy.py inherits a.py's sampled hash; it has no coverage record of its own.
    diff = "\n".join(
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1 +1,2 @@\n context\n+{'b' if path == 'b.py' else 'a'}\n"
        for path in paths
    )
    return ReviewContext(
        base_ref="new-base",
        head_ref="base-integration-head",
        unified_diff=diff,
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=0)
            for path in paths
        ],
    )


def _prior(*, verification: str = "verified") -> ReviewState:
    hashes = hashes_for_diffs(
        diffs=split_unified_diff_by_file(unified_diff=_context().unified_diff),
    )
    runs = tuple(
        RunRecord(
            identity=RunIdentity(
                round=number,
                model=_MODEL,
                provider="openai",
                transport="cli",
                lane="openai",
                endpoint="api.openai.com",
                first_party=True,
                model_verification=verification,
                reported_models=(_REPORTED,) if verification == "verified" else (),
            ),
        )
        for number in (1, 2)
    )
    return ReviewState(
        runs=runs,
        coverage=tuple(
            CoverageRecord(path=path, patch_hash=hashes[path], round=number)
            for number, path in enumerate(("a.py", "b.py"), start=1)
        ),
    )


def _run(
    prior: ReviewState,
    *,
    reported: tuple[ReportedModel, ...] = (),
    run_builtin_checklist: bool = True,
) -> ReviewResult:
    provider = MagicMock()
    provider.name = "openai"
    provider.model_name = _MODEL
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    provider.aclose = AsyncMock()
    # Keep the real invoke/ledger path; only the external provider is replaced.
    response = AIResponse(
        content='{"summary":"ok","checklist":[],"findings":[]}',
        model=_MODEL,
        provider="openai",
        reported_models=reported,
        input_tokens=10,
        output_tokens=5,
        cost_estimate=0.002,
        cost_reported=True,
    )
    with patch(
        "lintro.ai.invoke.complete_with_fallback",
        AsyncMock(return_value=response),
    ):
        return run_review(
            _context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
                depth=1,
                checklist_items=[],
                checklist_text="",
                classifications=[],
                prior_state=prior,
                enforce_cost_cap=False,
                run_builtin_checklist=run_builtin_checklist,
            ),
        )


def _persist(result: ReviewResult, prior: ReviewState, env: dict[str, str]) -> None:
    state = advance_review_state(
        request=StickyRequest(
            result=result,
            prior_state=prior,
            head_sha=_context().head_ref,
        ),
    )
    write_state_part(
        state=replace(
            state,
            run_id=env["GITHUB_RUN_ID"],
            run_attempt=env["GITHUB_RUN_ATTEMPT"],
        ),
        directory=Path(env["LINTRO_REVIEW_STATE_DIR"]),
        sequence=1,
        final=True,
    )


@pytest.mark.parametrize("verification", ["verified", "unreported"])
def test_all_carried_review_preserves_source_evidence_and_passes_ci(
    lane_env: dict[str, str],
    classifier: ModuleType,
    verification: str,
) -> None:
    """A base-only head carries two rounds and a sampled sibling, with no new call."""
    prior = _prior(verification=verification)
    result = _run(prior)
    assert result.coverage is not None
    assert result.coverage.to_dict() == {
        "reviewed": 0,
        "carried": 3,
        "awaiting": 0,
        "invalidated": 0,
        "eligible": 3,
        "covered_at_head": 3,
        "complete": True,
    }
    assert result.metadata.chunks_reviewed == 0
    assert result.metadata.model_verification == verification
    assert result.metadata.reported_models == prior.runs[0].identity.reported_models
    assert result.metadata.cost_estimate_usd == 0
    assert result.metadata.token_usage["total"] == 0
    assert not result.metadata.cost_reported
    _persist(result, prior, lane_env)
    report = classifier.classify(
        status=0,
        output=review_result_to_json(result=result),
        env=lane_env,
    )
    assert report.exit_code == 0
    assert report.outcome == classifier.ReviewOutcome.REVIEWED
    state_file = Path(lane_env["LINTRO_REVIEW_STATE_DIR"]) / "state.json"
    payload = json.loads(state_file.read_text())
    assert payload["runs"][-1]["model_verification"] == verification
    assert payload["runs"][-1].get("total", 0) == 0


@pytest.mark.parametrize(
    "defect",
    [
        "blank",
        "missing",
        "mismatch",
        "endpoint",
        "model",
        "lane",
        "legacy",
        "first_party",
        "missing_first_party",
        "unverifiable",
    ],
)
def test_missing_or_mismatched_source_requires_real_revalidation(
    lane_env: dict[str, str],
    classifier: ModuleType,
    defect: str,
) -> None:
    """A good source cannot vouch for another file with missing or bad evidence."""
    prior = _prior()
    bad = prior.runs[-1].identity
    if defect == "missing":
        prior = replace(prior, runs=prior.runs[:1])
    else:
        fields: dict[str, dict[str, Any]] = {
            "blank": {"model_verification": "", "reported_models": ()},
            "mismatch": {"model_verification": "mismatch"},
            "endpoint": {"endpoint": "proxy.invalid"},
            "model": {"reported_models": (ReportedModel(model="other-model"),)},
            "lane": {"lane": "anthropic"},
            "legacy": {"model_verification": "legacy"},
            "first_party": {"first_party": False},
            "missing_first_party": {"first_party": None},
            "unverifiable": {"model_verification": "unverifiable"},
        }
        prior = replace(
            prior,
            runs=(
                prior.runs[0],
                replace(prior.runs[-1], identity=replace(bad, **fields[defect])),
            ),
        )
    result = _run(prior)
    assert result.metadata.chunks_reviewed > 0
    # The revalidation returned no model evidence; the other source cannot
    # promote it to verified.
    assert result.metadata.model_verification == "unreported"
    strict_env = {
        **lane_env,
        "LINTRO_REVIEW_LANES": json.dumps(
            {"openai": {**_ROW, "reports_model_usage": True}},
        ),
    }
    _persist(result, prior, strict_env)
    report = classifier.classify(
        status=0,
        output=review_result_to_json(result=result),
        env=strict_env,
    )
    assert report.exit_code == 1
    assert report.status == "model-unreported"


def test_carried_evidence_uses_covering_rounds_and_keeps_the_weakest_state(
    lane_env: dict[str, str],
    classifier: ModuleType,
) -> None:
    """An unrelated latest record cannot replace a covering round's evidence."""
    prior = _prior()
    silent = replace(
        prior.runs[-1].identity,
        model_verification="unreported",
        reported_models=(),
    )
    unrelated = replace(prior.runs[0].identity, round=3, model_verification="mismatch")
    prior = replace(
        prior,
        runs=(prior.runs[0], RunRecord(identity=silent), RunRecord(identity=unrelated)),
    )
    result = _run(prior)
    assert result.metadata.model_verification == "unreported"
    assert result.metadata.reported_models == (_REPORTED,)
    _persist(result, prior, lane_env)
    report = classifier.classify(
        status=0,
        output=review_result_to_json(result=result),
        env=lane_env,
    )
    assert report.exit_code == 0


def test_revalidation_mismatch_cannot_be_hidden_by_good_carried_evidence(
    lane_env: dict[str, str],
    classifier: ModuleType,
) -> None:
    """Current provider evidence takes precedence over verified carried files."""
    prior = _prior()
    prior = replace(prior, coverage=prior.coverage[:1])
    wrong = ReportedModel(model="other-model", canonical="other-model")
    result = _run(prior, reported=(wrong,))
    assert result.metadata.chunks_reviewed > 0
    assert result.metadata.model_verification == "mismatch"
    assert result.metadata.reported_models == (wrong,)
    _persist(result, prior, lane_env)
    report = classifier.classify(
        status=0,
        output=review_result_to_json(result=result),
        env=lane_env,
    )
    assert report.exit_code == 1
    assert report.status == "model-mismatch"


def test_partial_carry_cannot_borrow_verified_evidence_for_unread_files(
    lane_env: dict[str, str],
    classifier: ModuleType,
) -> None:
    """A no-call result with an uncovered file stays incomplete and unverified."""
    prior = _prior()
    prior = replace(prior, coverage=prior.coverage[:1])
    result = _run(prior, run_builtin_checklist=False)
    assert result.coverage is not None
    assert result.coverage.carried == 2
    assert not result.coverage.complete
    assert result.metadata.model_verification == ""
    _persist(result, prior, lane_env)
    report = classifier.classify(
        status=0,
        output=review_result_to_json(result=result),
        env=lane_env,
    )
    assert report.exit_code == 1
    assert report.outcome == classifier.ReviewOutcome.INCOMPLETE


def test_carried_gateway_evidence_remains_unverifiable(
    lane_env: dict[str, str],
    classifier: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-call gateway round retains its actual state and the CI warning."""
    gateway_env = {
        **lane_env,
        "LINTRO_REVIEW_LANE": "zai",
        "LINTRO_REVIEW_ENDPOINT": "api.z.ai",
        "LINTRO_REVIEW_FIRST_PARTY": "false",
        "OPENAI_BASE_URL": "https://api.z.ai",
        "LINTRO_REVIEW_CHAIN": '["zai"]',
        "LINTRO_REVIEW_LANES": json.dumps(
            {"zai": {**_ROW, "endpoint": "https://api.z.ai", "first_party": False}},
        ),
    }
    for key, value in gateway_env.items():
        monkeypatch.setenv(key, value)
    prior = _prior(verification="unverifiable")
    prior = replace(
        prior,
        runs=tuple(
            replace(
                run,
                identity=replace(
                    run.identity,
                    lane="zai",
                    endpoint="api.z.ai",
                    first_party=False,
                ),
            )
            for run in prior.runs
        ),
    )
    result = _run(prior)
    assert result.metadata.chunks_reviewed == 0
    assert result.metadata.model_verification == "unverifiable"
    assert not result.metadata.first_party
    assert result.metadata.reported_models == ()
    _persist(result, prior, gateway_env)
    report = classifier.classify(
        status=0,
        output=review_result_to_json(result=result),
        env=gateway_env,
    )
    assert report.exit_code == 0
    assert any("cannot be verified" in warning for warning in report.warnings)


@pytest.mark.parametrize("field", ["run_id", "run_attempt"])
def test_carried_review_still_requires_a_current_attempt_record(
    lane_env: dict[str, str],
    classifier: ModuleType,
    field: str,
) -> None:
    """Carried evidence never makes a prior artifact count as a current record."""
    prior = _prior()
    result = _run(prior)
    _persist(result, prior, lane_env)
    state_file = Path(lane_env["LINTRO_REVIEW_STATE_DIR"]) / "state.json"
    payload = json.loads(state_file.read_text())
    payload[field] = "earlier"
    state_file.write_text(json.dumps(payload))
    report = classifier.classify(
        status=0,
        output=review_result_to_json(result=result),
        env=lane_env,
    )
    assert report.exit_code == 1
    assert report.status == "provenance-unrecorded"
