"""Which stored rounds a run may inherit coverage from (#2867).

ADR-0011 decision B: review-reuse validity includes the lane, and a
``legacy``, ``mismatch`` or (on a reporting lane) ``unreported`` round is
re-reviewed rather than carried into a green check. Before coverage is
inherited every stored round is re-checked against the current lane row.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.enums import CredentialKind
from lintro.ai.provenance import RunProvenance
from lintro.ai.providers.reported_model import ReportedModel
from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.coverage_rounds import hashes_for_diffs
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.resume import plan_resume
from lintro.ai.review.reuse_gate import (
    agent_model_overrides,
    allowed_models_for,
    reusable_rounds,
    reusable_rounds_for,
)

_OPUS = ReportedModel(model="claude-opus-5-5", canonical="claude-opus-5-5")
_HAIKU = ReportedModel(model="claude-haiku-4-5", canonical="claude-haiku-4-5")

_CURRENT = RunProvenance(
    endpoint="api.anthropic.com",
    first_party=True,
    credential=CredentialKind.CLAUDE_OAUTH,
    lane="claude-subscription",
    reports_model_usage=True,
)


def _run(
    round_number: int,
    *,
    state: str = "verified",
    lane: str = "claude-subscription",
    endpoint: str = "api.anthropic.com",
    reported: tuple[ReportedModel, ...] = (_OPUS,),
) -> RunRecord:
    return RunRecord(
        identity=RunIdentity(
            round=round_number,
            model="claude-opus-5-5",
            lane=lane,
            endpoint=endpoint,
            first_party=endpoint == "api.anthropic.com",
            reported_models=reported,
            model_verification=state,
        ),
    )


def _rounds(*runs: RunRecord) -> frozenset[int]:
    return reusable_rounds(
        prior=ReviewState(runs=runs),
        provenance=_CURRENT,
        allowed=("claude-opus-5-5",),
    )


def test_a_verified_round_on_the_same_lane_is_reusable() -> None:
    """The ordinary case: same lane, same endpoint, allowed model, verified."""
    assert_that(_rounds(_run(1))).is_equal_to(frozenset({1}))


@pytest.mark.parametrize("state", ["legacy", "mismatch"])
def test_legacy_and_mismatched_rounds_are_never_reused(state: str) -> None:
    """A pre-ADR round or one answered by a disallowed model is re-reviewed."""
    assert_that(_rounds(_run(1, state=state), _run(2))).is_equal_to(frozenset({2}))


def test_an_unreported_round_is_reused_only_where_the_lane_cannot_report() -> None:
    """On a reporting lane an unreported round is a transport defect."""
    reporting = reusable_rounds(
        prior=ReviewState(runs=(_run(1, state="unreported"),)),
        provenance=_CURRENT,
        allowed=("claude-opus-5-5",),
    )
    silent_lane = reusable_rounds(
        prior=ReviewState(runs=(_run(1, state="unreported"),)),
        provenance=RunProvenance(
            endpoint="api.anthropic.com",
            first_party=True,
            lane="claude-subscription",
            reports_model_usage=False,
        ),
        allowed=("claude-opus-5-5",),
    )

    assert_that(reporting).is_empty()
    assert_that(silent_lane).is_equal_to(frozenset({1}))


def test_a_round_from_another_lane_or_endpoint_is_not_reused() -> None:
    """Reuse validity includes the lane: a lane switch re-reviews once."""
    rounds = _rounds(
        _run(1, lane="codex-subscription"),
        _run(2, endpoint="api.z.ai"),
        _run(3),
    )

    assert_that(rounds).is_equal_to(frozenset({3}))


def test_tightening_the_allowed_models_re_reviews_what_they_covered() -> None:
    """A stored verified round is re-checked against the current lane row."""
    rounds = reusable_rounds(
        prior=ReviewState(runs=(_run(1, reported=(_OPUS, _HAIKU)), _run(2))),
        provenance=_CURRENT,
        allowed=("claude-opus-5-5",),
    )

    assert_that(rounds).is_equal_to(frozenset({2}))


def test_a_gateway_lane_does_not_re_check_models_it_never_trusted() -> None:
    """Off a first-party lane the reported models are not evidence either way."""
    gateway = RunProvenance(
        endpoint="api.z.ai",
        first_party=False,
        lane="zai-glm-gateway",
        reports_model_usage=True,
    )
    rounds = reusable_rounds(
        prior=ReviewState(
            runs=(
                _run(
                    1,
                    state="unverifiable",
                    lane="zai-glm-gateway",
                    endpoint="api.z.ai",
                ),
            ),
        ),
        provenance=gateway,
        allowed=("glm-5.3-flash",),
    )

    assert_that(rounds).is_equal_to(frozenset({1}))


def test_no_prior_state_gates_nothing() -> None:
    """A first round has nothing to inherit."""
    assert_that(reusable_rounds(prior=None, provenance=_CURRENT, allowed=())).is_empty()


def test_coverage_with_no_run_record_to_place_it_is_not_inherited() -> None:
    """A coverage-only prior predates the gate or was hand-built: re-review."""
    options = MagicMock()
    options.prior_state = ReviewState(
        coverage=(CoverageRecord("a.py", "h1", round=1),),
    )

    assert_that(reusable_rounds_for(options=options)).is_equal_to(frozenset())
    options.prior_state = None
    assert_that(reusable_rounds_for(options=options)).is_none()


def test_the_lane_rows_allowed_list_wins_over_the_declared_fallbacks() -> None:
    """Outside a lane the default model and its fallbacks are what is allowed."""
    local = allowed_models_for(
        provenance=RunProvenance(),
        requested="claude-opus-5-5",
        fallback_models=("claude-sonnet-4-6", " "),
    )
    lane = allowed_models_for(
        provenance=RunProvenance(allowed_models=("glm-5.3-flash",)),
        requested="claude-opus-5-5",
        fallback_models=("claude-sonnet-4-6",),
    )

    assert_that(local).is_equal_to(("claude-opus-5-5", "claude-sonnet-4-6"))
    assert_that(lane).is_equal_to(("glm-5.3-flash",))


# --- plan_resume honours the gate ----------------------------------------------

_DIFF = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n x = 1\n+y = 2\n"
)


def _context() -> ReviewContext:
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff=_DIFF,
        pr_metadata=None,
    )


def _prior() -> ReviewState:
    patch_hash = hashes_for_diffs(diffs=split_unified_diff_by_file(unified_diff=_DIFF))[
        "a.py"
    ]
    return ReviewState(
        runs=(_run(1, state="legacy"), _run(2)),
        coverage=(CoverageRecord("a.py", patch_hash, reviewed_sha="old", round=1),),
    )


def test_coverage_from_an_excluded_round_is_queued_again() -> None:
    """A legacy round's coverage no longer satisfies an unchanged file."""
    ungated = plan_resume(context=_context(), prior=_prior())
    gated = plan_resume(
        context=_context(),
        prior=_prior(),
        reusable_rounds=frozenset({2}),
    )

    assert_that(ungated.queue).is_empty()
    assert_that(gated.queue).is_equal_to(("a.py",))


def test_coverage_from_a_reusable_round_is_still_inherited() -> None:
    """The gate only removes what it names."""
    plan = plan_resume(
        context=_context(),
        prior=_prior(),
        reusable_rounds=frozenset({1, 2}),
    )

    assert_that(plan.queue).is_empty()


# --- round-two review findings ------------------------------------------------


def test_an_unrecognised_stored_state_is_not_reused() -> None:
    """A corrupt or newer state means more review, not less."""
    assert_that(_rounds(_run(1, state="bogus"), _run(2))).is_equal_to(frozenset({2}))


def test_custom_agent_overrides_are_allowed_outside_a_lane_row() -> None:
    """A custom agent's ``model`` is a legitimate requested model (ADR-0011 B)."""
    allowed = allowed_models_for(
        provenance=RunProvenance(),
        requested="claude-opus-5-5",
        fallback_models=(),
        agent_models=("claude-sonnet-4-6", "claude-sonnet-4-6"),
    )

    assert_that(allowed).is_equal_to(("claude-opus-5-5", "claude-sonnet-4-6"))


def test_agent_model_overrides_are_read_from_the_session_options() -> None:
    """Distinct overrides, in agent order; agents without one add nothing."""
    options = MagicMock()
    options.custom_agents = (
        MagicMock(model="claude-sonnet-4-6"),
        MagicMock(model=""),
        MagicMock(model="claude-haiku-4-5"),
        MagicMock(model="claude-sonnet-4-6"),
    )

    assert_that(agent_model_overrides(options)).is_equal_to(
        ("claude-sonnet-4-6", "claude-haiku-4-5"),
    )


def test_a_stored_alias_cannot_vouch_for_a_different_canonical_model() -> None:
    """The reuse re-check uses canonical names only, like live verification."""
    alias = ReportedModel(model="claude-opus-5-5", canonical="glm-5")

    assert_that(_rounds(_run(1, reported=(alias,)))).is_empty()


def test_a_record_with_no_reported_models_is_vouched_for_by_its_request() -> None:
    """A narrowed allowed list cannot be met vacuously by an empty report."""
    assert_that(_rounds(_run(1, reported=()))).is_equal_to(frozenset({1}))
    narrowed = reusable_rounds(
        prior=ReviewState(runs=(_run(1, reported=()),)),
        provenance=_CURRENT,
        allowed=("claude-sonnet-4-6",),
    )
    assert_that(narrowed).is_empty()


def test_excluded_coverage_is_dropped_at_persistence_not_only_at_planning() -> None:
    """Coverage from an excluded round must not survive under a reused number."""
    from lintro.ai.review.resume import records_for_reviewed

    plan = plan_resume(
        context=_context(),
        prior=_prior(),
        reusable_rounds=frozenset({2}),
    )

    persisted = records_for_reviewed(
        plan=plan,
        reviewed_paths=(),
        head_sha="feature",
        round_number=1,
        prior=_prior(),
    )

    assert_that(plan.coverage).is_empty()
    assert_that(persisted).is_empty()
