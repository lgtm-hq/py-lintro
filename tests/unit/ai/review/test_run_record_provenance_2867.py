"""The run record carries provenance and splits shadow cost from spend (#2867).

ADR-0011 decision B: a stored round says where it went and what answered, a
record from before the ADR reads as ``legacy``, and a ``list_price_shadow``
or ``unknown`` figure settles the caps without ever being spend.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from assertpy import assert_that

from lintro.ai.enums import CostBasis, ModelVerification
from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.review.cost_cap import cap_is_enforced
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.pr_budget import resolve_pr_budget
from lintro.ai.review.review_state_codec import decode_state, leftover_state_block
from lintro.ai.review.run_record_factory import RoundTotals, run_record_from_result
from lintro.ai.review.state_store import union_states

# --- flat codec ---------------------------------------------------------------


def test_provenance_fields_round_trip_through_the_flat_payload() -> None:
    """Every decision-B field survives serialize/parse, including a False."""
    record = RunRecord(
        identity=RunIdentity(
            round=2,
            endpoint="api.z.ai",
            first_party=False,
            credential="zai_token",
            reported_models=("glm-5.3-flash",),
            lane="zai-glm-gateway",
            model_verification="unverifiable",
        ),
        usage=RunUsage(cost=0.0, cost_basis="unknown", cost_shadow=0.42),
    )

    payload = record.to_dict()
    restored = RunRecord.from_dict(payload)

    assert_that(payload["first_party"]).is_false()
    assert_that(payload["reported_models"]).is_equal_to(["glm-5.3-flash"])
    assert_that(payload["cost_shadow"]).is_equal_to(0.42)
    assert_that(restored).is_equal_to(record)


def test_a_record_from_before_the_adr_reads_as_legacy() -> None:
    """A blob carrying none of the new keys is a pre-ADR round (ADR-0011 B)."""
    restored = RunRecord.from_dict({"round": 1, "sha": "abc", "model": "claude"})

    assert_that(restored.identity.model_verification).is_equal_to(
        ModelVerification.LEGACY.value,
    )
    assert_that(restored.identity.first_party).is_none()
    assert_that(restored.identity.endpoint).is_empty()


def test_a_record_with_provenance_but_no_state_is_not_legacy() -> None:
    """A round that completed no call recorded no state; that is not ``legacy``."""
    restored = RunRecord.from_dict(
        {"round": 1, "endpoint": "api.anthropic.com", "first_party": True},
    )

    assert_that(restored.identity.model_verification).is_empty()
    assert_that(restored.identity.first_party).is_true()


@pytest.mark.parametrize("raw", ["bogus", 3, ["verified"]])
def test_an_unrecognised_state_is_dropped_not_guessed(raw: object) -> None:
    """A corrupt state value reads as unknown rather than as a valid state."""
    restored = RunRecord.from_dict({"round": 1, "model_verification": raw})

    assert_that(restored.identity.model_verification).is_empty()


def test_a_live_record_never_writes_the_legacy_state_by_default() -> None:
    """A default record writes no provenance keys at all."""
    payload = RunRecord().to_dict()

    for key in ("endpoint", "first_party", "model_verification", "cost_shadow"):
        assert_that(payload).does_not_contain_key(key)


def test_first_party_is_written_only_beside_an_endpoint() -> None:
    """Without an endpoint there is nothing for ``first_party`` to describe."""
    payload = RunRecord(identity=RunIdentity(first_party=True)).to_dict()

    assert_that(payload).does_not_contain_key("first_party")


# --- factory ------------------------------------------------------------------


def _record(result: ReviewResult, *, cost_basis: str) -> RunRecord:
    return run_record_from_result(
        request=StickyRequest(
            result=result,
            head_sha="abc1234",
            transport="cli",
            auth_mode="subscription",
            cost_basis=cost_basis,
        ),
        totals=RoundTotals(
            round_number=1,
            verdict=ReviewVerdict.READY,
            resolved=0,
            open_after=0,
            convergence_score=0.0,
        ),
    )


def test_the_factory_copies_provenance_from_the_metadata(
    sample_review_result: ReviewResult,
) -> None:
    """What the run stamped on its metadata is what the record carries."""
    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            endpoint="api.anthropic.com",
            first_party=True,
            credential="claude_oauth",
            reported_models=("claude-sonnet-4-20250514",),
            lane="claude-subscription",
            model_verification="verified",
        ),
    )

    identity = _record(result, cost_basis="list_price_shadow").identity

    assert_that(identity.endpoint).is_equal_to("api.anthropic.com")
    assert_that(identity.first_party).is_true()
    assert_that(identity.credential).is_equal_to("claude_oauth")
    assert_that(identity.reported_models).is_equal_to(("claude-sonnet-4-20250514",))
    assert_that(identity.lane).is_equal_to("claude-subscription")
    assert_that(identity.model_verification).is_equal_to("verified")


@pytest.mark.parametrize("basis", ["list_price_shadow", "unknown"])
def test_a_shadow_basis_records_the_figure_as_shadow_not_spend(
    sample_review_result: ReviewResult,
    basis: str,
) -> None:
    """On a shadow basis ``cost`` is zero and the figure lives in ``cost_shadow``."""
    usage = _record(sample_review_result, cost_basis=basis).usage

    assert_that(usage.cost).is_equal_to(0.0)
    assert_that(usage.cost_shadow).is_equal_to(
        sample_review_result.metadata.cost_estimate_usd,
    )
    assert_that(usage.cost_basis).is_equal_to(basis)


@pytest.mark.parametrize("basis", ["billed", "estimated", "unpriceable"])
def test_a_priced_or_unpriced_basis_keeps_the_figure_as_spend(
    sample_review_result: ReviewResult,
    basis: str,
) -> None:
    """The pre-ADR bases are unchanged: the figure is the round's cost."""
    usage = _record(sample_review_result, cost_basis=basis).usage

    assert_that(usage.cost).is_equal_to(sample_review_result.metadata.cost_estimate_usd)
    assert_that(usage.cost_shadow).is_equal_to(0.0)


# --- state accounting ---------------------------------------------------------


def _shadow_state(*shadows: float, stored: float = 0.0) -> ReviewState:
    runs = tuple(
        RunRecord(usage=RunUsage(cost_basis="list_price_shadow", cost_shadow=value))
        for value in shadows
    )
    return ReviewState(runs=runs, pr_shadow_usd=stored)


def test_the_shadow_total_never_drops_below_the_runs_sum() -> None:
    """Like ``pr_spend_usd``, the shadow total is monotonic over pruning."""
    assert_that(_shadow_state(0.3, 0.2).review_shadow_usd).is_close_to(0.5, 1e-9)
    assert_that(_shadow_state(0.3, stored=0.9).review_shadow_usd).is_equal_to(0.9)


def test_the_shadow_total_round_trips_through_the_sticky_blob() -> None:
    """The cumulative shadow is stored beside the cumulative spend."""
    state = _shadow_state(0.25, stored=0.75)

    decoded = decode_state(body=leftover_state_block(state=state))

    assert_that(decoded.pr_shadow_usd).is_equal_to(0.75)
    assert_that(decoded.review_shadow_usd).is_equal_to(0.75)


def test_the_shadow_total_round_trips_through_the_artifact() -> None:
    """The artifact envelope carries the shadow like the spend."""
    state = _shadow_state(0.25, stored=0.75)

    restored = ReviewState.from_artifact_dict(state.to_artifact_dict())

    assert_that(restored.pr_shadow_usd).is_equal_to(0.75)


def test_merging_parts_keeps_the_highest_shadow_seen() -> None:
    """A later part never lowers the PR's shadow figure."""
    merged = union_states(
        [_shadow_state(stored=0.5), _shadow_state(stored=0.2)],
    )

    assert_that(merged.review_shadow_usd).is_equal_to(0.5)


def test_an_empty_shadow_is_not_written() -> None:
    """A PR that never ran a shadow round carries no ``pr_shadow_usd`` key."""
    assert_that(ReviewState().to_dict()).does_not_contain_key("pr_shadow_usd")


# --- budget and cap -----------------------------------------------------------


def test_prior_spend_adds_the_shadow_to_the_spend() -> None:
    """A shadow round is not billed, but it counts against the PR's budget."""
    state = ReviewState(
        runs=(
            RunRecord(usage=RunUsage(cost=1.0, cost_basis="billed")),
            RunRecord(
                usage=RunUsage(
                    cost=0.0,
                    cost_basis="list_price_shadow",
                    cost_shadow=2.0,
                ),
            ),
        ),
    )

    budget = resolve_pr_budget(
        budget_usd=10.0,
        source=ConfigSource.ENV,
        basis=CostBasis.LIST_PRICE_SHADOW,
        prior_state=state,
    )

    assert budget is not None
    assert_that(budget.prior_spend_usd).is_close_to(3.0, 1e-9)
    assert_that(budget.unpriceable).is_true()


def test_a_gateway_basis_is_real_money_not_a_runtime_bound() -> None:
    """``unknown`` bills a gateway, so it is neither unpriceable nor advisory."""
    budget = resolve_pr_budget(
        budget_usd=10.0,
        source=ConfigSource.CONFIG,
        basis=CostBasis.UNKNOWN,
        prior_state=None,
    )

    assert budget is not None
    assert_that(budget.unpriceable).is_false()
    assert_that(budget.enforced).is_true()


@pytest.mark.parametrize(
    ("source", "basis", "enforced"),
    [
        (ConfigSource.CONFIG, CostBasis.UNKNOWN, True),
        (ConfigSource.CONFIG, CostBasis.LIST_PRICE_SHADOW, False),
        (ConfigSource.ENV, CostBasis.LIST_PRICE_SHADOW, True),
        (ConfigSource.FLAG, CostBasis.UNKNOWN, True),
    ],
)
def test_yaml_caps_enforce_on_unknown_and_stay_advisory_on_the_shadow(
    source: ConfigSource,
    basis: CostBasis,
    enforced: bool,
) -> None:
    """ADR-0007 rule 3 extended: the new bases follow the money (ADR-0011 F)."""
    assert_that(cap_is_enforced(source=source, basis=basis)).is_equal_to(enforced)
