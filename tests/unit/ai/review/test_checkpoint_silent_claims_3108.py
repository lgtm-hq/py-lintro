"""Silent model claims survive persisted checkpoint replacement (#3108)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.provenance import LedgerCall, ModelLedger, RunProvenance, model_ledger
from lintro.ai.providers.reported_model import ReportedModel
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.incremental_coverage import write_incremental_coverage_part
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.resume import ResumePlan, plan_resume, records_for_reviewed
from lintro.ai.review.reuse_gate import reusable_rounds
from lintro.ai.review.run_provenance import checkpoint_stamp, stamp_run_provenance
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.state_store import load_ci_state, write_state_part
from lintro.ai.review.sticky.assembly import advance_review_state

_REPO = "lgtm-hq/py-lintro"
_PRIMARY = "gpt-6.1-sol"
_OVERRIDE = "gpt-6.1-terra"
_ALLOWED = (_PRIMARY, _OVERRIDE)
_REPORTED = ReportedModel(model=_PRIMARY, canonical=_PRIMARY)
_SILENT = LedgerCall(requested=_OVERRIDE)
_VERIFIED = LedgerCall(requested=_PRIMARY, reported=(_REPORTED,))
_PROVENANCE = RunProvenance(
    endpoint="api.openai.com",
    first_party=True,
    lane="openai",
    reports_model_usage=False,
)


@pytest.fixture
def artifact_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the lane explicit and all checkpoint artifacts inside the test dir."""
    for key, value in {
        "LINTRO_REVIEW_STATE_DIR": str(tmp_path),
        "GITHUB_REPOSITORY": _REPO,
        "LINTRO_REVIEW_LANE": "openai",
        "LINTRO_REVIEW_ENDPOINT": "api.openai.com",
        "LINTRO_REVIEW_FIRST_PARTY": "true",
        "LINTRO_REVIEW_REPORTS_MODEL_USAGE": "false",
        "LINTRO_REVIEW_ALLOWED_MODELS": ",".join(_ALLOWED),
    }.items():
        monkeypatch.setenv(key, value)
    return tmp_path


def _context() -> ReviewContext:
    paths = ("a.py", "b.py", "c.py")
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        unified_diff="\n".join(
            f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1 +1,2 @@\n context\n+change_{index}\n"
            for index, path in enumerate(paths)
        ),
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=0)
            for path in paths
        ],
    )


def _options(prior: ReviewState | None) -> ReviewSessionOptions:
    provider = MagicMock(name="provider", model_name=_PRIMARY)
    provider.name = "openai"
    return ReviewSessionOptions(
        provider=provider,
        ai_config=AIConfig(transport=AITransport.CLI),
        depth=1,
        checklist_items=[],
        checklist_text="",
        classifications=[],
        prior_state=prior,
    )


def _plan(prior: ReviewState | None, allowed: tuple[str, ...] = _ALLOWED) -> ResumePlan:
    return plan_resume(
        context=_context(),
        prior=prior,
        reusable_rounds=reusable_rounds(
            prior=prior,
            provenance=_PROVENANCE,
            allowed=allowed,
        ),
    )


def _checkpoint(
    directory: Path,
    *,
    prior: ReviewState | None,
    calls: tuple[LedgerCall, ...],
    reviewed: tuple[str, ...],
    allowed: tuple[str, ...] = _ALLOWED,
) -> ReviewState:
    stamp = checkpoint_stamp(options=_options(prior), cost_basis="unpriceable")
    with model_ledger() as ledger:
        ledger.calls.extend(calls)
        write_incremental_coverage_part(
            collected=[
                ChunkReviewPartial(
                    findings=(),
                    input_tokens=0,
                    output_tokens=0,
                    cost_estimate=0.0,
                    files=reviewed,
                ),
            ],
            resume=_plan(prior, allowed),
            context=_context(),
            prior_state=prior,
            force_full=False,
            sequence=1,
            policy=resolve_sensitivity_policy(strictness=ReviewStrictness.BALANCED),
            stamp=replace(stamp, pr_number=3108),
        )
    return load_ci_state(directory=directory, repo=_REPO, pr_number=3108)


def _finish(
    directory: Path,
    *,
    prior: ReviewState,
    result: ReviewResult,
    calls: tuple[LedgerCall, ...] = (_VERIFIED,),
    allowed: tuple[str, ...] = _ALLOWED,
) -> ReviewState:
    plan = _plan(prior, allowed)
    result = replace(
        result,
        metadata=replace(
            result.metadata,
            model=_PRIMARY,
            provider="openai",
            partial=False,
            reviewed_paths=plan.queue,
        ),
        coverage=plan.counts(reviewed_now=plan.queue),
        coverage_records=records_for_reviewed(
            plan=plan,
            reviewed_paths=plan.queue,
            head_sha="feature",
            round_number=prior.next_round,
            prior=prior,
        ),
    )
    result = stamp_run_provenance(
        result=result,
        options=_options(prior),
        ledger=ModelLedger(calls=list(calls)),
        resume=plan,
    )
    state = advance_review_state(
        request=StickyRequest(result=result, prior_state=prior, head_sha="feature"),
    )
    write_state_part(state=state, directory=directory, sequence=1, final=True)
    return load_ci_state(directory=directory, repo=_REPO, pr_number=3108)


@pytest.mark.parametrize("mixed", [False, True])
def test_checkpoint_serializes_silent_override_claims(
    artifact_dir: Path,
    mixed: bool,
) -> None:
    """A default model cannot stand in for a silent agent override on disk."""
    loaded = _checkpoint(
        artifact_dir,
        prior=None,
        calls=(_SILENT, _VERIFIED, _SILENT) if mixed else (_SILENT,),
        reviewed=("a.py",),
    )

    for filename in ("part-0001.json", "state.json"):
        payload = json.loads((artifact_dir / filename).read_text())
        assert_that(payload["runs"][0]["unreported_models"]).is_equal_to([_OVERRIDE])
    identity = loaded.runs[0].identity
    assert_that(identity.model_verification).is_equal_to("unreported")
    assert_that(identity.reported_models).is_equal_to((_REPORTED,) if mixed else ())
    assert_that(_plan(loaded).queue).is_equal_to(("b.py", "c.py"))
    assert_that(_plan(loaded, (_PRIMARY,)).queue).is_equal_to(("a.py", "b.py", "c.py"))


@pytest.mark.parametrize("replacement", ["checkpoint", "final"])
def test_verified_replacement_keeps_silent_sources_unreported(
    artifact_dir: Path,
    sample_review_result: ReviewResult,
    replacement: str,
) -> None:
    """Replacement preserves claims separately and never upgrades silent evidence."""
    prior = _checkpoint(
        artifact_dir,
        prior=None,
        calls=(_SILENT,),
        reviewed=("a.py",),
    )
    if replacement == "checkpoint":
        loaded = _checkpoint(
            artifact_dir,
            prior=prior,
            calls=(_VERIFIED,),
            reviewed=("b.py",),
        )
    else:
        loaded = _finish(artifact_dir, prior=prior, result=sample_review_result)

    assert_that(loaded.runs).is_length(1)
    identity = loaded.runs[0].identity
    assert_that(identity.unreported_models).is_equal_to((_OVERRIDE,))
    assert_that(identity.reported_models).is_equal_to((_REPORTED,))
    assert_that(identity.model_verification).is_equal_to("unreported")
    assert_that(_plan(loaded, (_PRIMARY,)).queue).is_equal_to(("a.py", "b.py", "c.py"))
    reporting = reusable_rounds(
        prior=loaded,
        provenance=replace(_PROVENANCE, reports_model_usage=True),
        allowed=_ALLOWED,
    )
    assert_that(reporting).is_empty()


def test_repeated_partial_resume_retains_claims_and_allowed_coverage(
    artifact_dir: Path,
    sample_review_result: ReviewResult,
) -> None:
    """Two interrupts plus final persistence keep mixed sources reusable safely."""
    first = _checkpoint(
        artifact_dir,
        prior=None,
        calls=(_SILENT,),
        reviewed=("a.py",),
    )
    second = _checkpoint(
        artifact_dir,
        prior=first,
        calls=(_VERIFIED,),
        reviewed=("b.py",),
    )
    assert_that(_plan(second).queue).is_equal_to(("c.py",))
    final = _finish(artifact_dir, prior=second, result=sample_review_result)

    assert_that(final.next_round).is_equal_to(2)
    assert_that(final.runs[0].coverage.checkpoint).is_false()
    assert_that(final.runs[0].identity.unreported_models).is_equal_to((_OVERRIDE,))
    assert_that(final.runs[0].identity.reported_models).is_equal_to((_REPORTED,))
    assert_that(final.runs[0].identity.model_verification).is_equal_to("unreported")
    assert_that(_plan(final).queue).is_empty()
    assert_that(_plan(final).counts(reviewed_now=()).carried).is_equal_to(3)
    assert_that(_plan(final, (_PRIMARY,)).coverage).is_empty()


@pytest.mark.parametrize(
    ("claims", "mixed", "expected", "inherited"),
    [
        ({}, False, (_OVERRIDE,), True),
        ({}, True, (), False),
        ({"unreported_models": None}, False, (), False),
        ({"unreported_models": [None]}, False, (), False),
    ],
)
def test_old_and_malformed_checkpoint_claims_remain_distinct_after_replacement(
    artifact_dir: Path,
    sample_review_result: ReviewResult,
    claims: dict[str, object],
    mixed: bool,
    expected: tuple[str, ...],
    inherited: bool,
) -> None:
    """Only an old purely silent request has an unambiguous fallback claim."""
    prior = _checkpoint(
        artifact_dir,
        prior=None,
        calls=(_SILENT,),
        reviewed=("a.py",),
    )
    payload = prior.runs[0].to_dict()
    payload.pop("unreported_models", None)
    payload.update(claims)
    payload["model"] = _OVERRIDE
    if mixed:
        payload["reported_models"] = [_PRIMARY]
    prior = replace(prior, runs=(RunRecord.from_dict(payload),))
    assert_that(bool(_plan(prior).coverage)).is_equal_to(inherited)
    if "unreported_models" in claims:
        assert_that(prior.runs[0].identity.unreported_models).is_equal_to(("",))
    else:
        assert_that(prior.runs[0].identity.unreported_models).is_empty()

    loaded = _finish(artifact_dir, prior=prior, result=sample_review_result)

    assert_that(loaded.runs[0].identity.unreported_models).is_equal_to(expected)
    assert_that(loaded.runs[0].identity.reported_models).is_equal_to((_REPORTED,))
    assert_that(_plan(loaded).queue).is_empty()
    assert_that(loaded.runs[0].identity.model_verification).is_equal_to(
        "unreported" if inherited else "verified",
    )
    assert_that(bool(_plan(loaded, (_PRIMARY,)).coverage)).is_equal_to(not inherited)


@pytest.mark.parametrize("replacement", ["checkpoint", "final"])
def test_narrowed_policy_can_replace_a_fully_rereviewed_silent_checkpoint(
    artifact_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_review_result: ReviewResult,
    replacement: str,
) -> None:
    """Once all excluded coverage is re-read, its old claims no longer apply."""
    prior = _checkpoint(
        artifact_dir,
        prior=None,
        calls=(_SILENT,),
        reviewed=("a.py",),
    )
    monkeypatch.setenv("LINTRO_REVIEW_ALLOWED_MODELS", _PRIMARY)
    if replacement == "checkpoint":
        loaded = _checkpoint(
            artifact_dir,
            prior=prior,
            calls=(_VERIFIED,),
            reviewed=("a.py", "b.py", "c.py"),
            allowed=(_PRIMARY,),
        )
    else:
        loaded = _finish(
            artifact_dir,
            prior=prior,
            result=sample_review_result,
            allowed=(_PRIMARY,),
        )

    assert_that(loaded.runs[0].identity.unreported_models).is_empty()
    assert_that(loaded.runs[0].identity.model_verification).is_equal_to("verified")
    assert_that(_plan(loaded, (_PRIMARY,)).queue).is_empty()


@pytest.mark.parametrize("replacement", ["checkpoint", "final"])
@pytest.mark.parametrize("state", ["", "mismatch"])
def test_inherited_silent_claims_cannot_hide_a_live_missing_or_mismatched_state(
    artifact_dir: Path,
    sample_review_result: ReviewResult,
    replacement: str,
    state: str,
) -> None:
    """Retaining claims never upgrades an empty ledger or a real mismatch."""
    prior = _checkpoint(
        artifact_dir,
        prior=None,
        calls=(_SILENT,),
        reviewed=("a.py",),
    )
    rogue = ReportedModel(model="unexpected-model", canonical="unexpected-model")
    calls = (LedgerCall(requested=_PRIMARY, reported=(rogue,)),) if state else ()
    if replacement == "checkpoint":
        loaded = _checkpoint(
            artifact_dir,
            prior=prior,
            calls=calls,
            reviewed=("b.py",),
        )
    else:
        loaded = _finish(
            artifact_dir,
            prior=prior,
            result=sample_review_result,
            calls=calls,
        )

    assert_that(loaded.runs[0].identity.model_verification).is_equal_to(state)
    assert_that(loaded.runs[0].identity.unreported_models).is_equal_to((_OVERRIDE,))
    assert_that(loaded.runs[0].identity.reported_models).is_equal_to(
        (rogue,) if state else (),
    )
    assert_that(_plan(loaded).coverage).is_empty()


@pytest.mark.parametrize(
    "change",
    [
        {"model_verification": "mismatch"},
        {"model_verification": ""},
        {"lane": "other"},
        {"endpoint": "gateway.example"},
        {"first_party": False},
    ],
)
def test_excluded_silent_checkpoint_does_not_taint_clean_rereview(
    artifact_dir: Path,
    sample_review_result: ReviewResult,
    change: dict[str, object],
) -> None:
    """State, lane, endpoint and first-party checks still force a full re-read."""
    prior = _checkpoint(
        artifact_dir,
        prior=None,
        calls=(_SILENT,),
        reviewed=("a.py",),
    )
    prior = replace(
        prior,
        runs=(RunRecord.from_dict({**prior.runs[0].to_dict(), **change}),),
    )
    assert_that(_plan(prior).queue).is_equal_to(("a.py", "b.py", "c.py"))

    loaded = _finish(artifact_dir, prior=prior, result=sample_review_result)

    assert_that(loaded.runs[0].identity.model_verification).is_equal_to("verified")
    assert_that(loaded.runs[0].identity.unreported_models).is_empty()
    assert_that(loaded.runs[0].identity.reported_models).is_equal_to((_REPORTED,))
    assert_that(_plan(loaded, (_PRIMARY,)).queue).is_empty()


def test_multiple_silent_attempts_merge_claims_without_reported_evidence(
    artifact_dir: Path,
) -> None:
    """A later silent default request cannot erase a silent agent override."""
    first = _checkpoint(
        artifact_dir,
        prior=None,
        calls=(_SILENT,),
        reviewed=("a.py",),
    )
    second = _checkpoint(
        artifact_dir,
        prior=first,
        calls=(LedgerCall(requested=_PRIMARY),),
        reviewed=("b.py",),
    )

    identity = second.runs[0].identity
    assert_that(identity.unreported_models).is_equal_to((_PRIMARY, _OVERRIDE))
    assert_that(identity.reported_models).is_empty()
    assert_that(identity.model_verification).is_equal_to("unreported")
    assert_that(_plan(second).queue).is_equal_to(("c.py",))
    assert_that(_plan(second, (_PRIMARY,)).coverage).is_empty()
