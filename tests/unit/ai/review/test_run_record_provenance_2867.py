"""The run record carries provenance and splits shadow cost from spend (#2867).

ADR-0011 decision B: a stored round says where it went and what answered, a
record from before the ADR reads as ``legacy``, and a ``list_price_shadow``
or ``unknown`` figure settles the caps without ever being spend.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport, CostBasis, ModelVerification
from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.providers.reported_model import ReportedModel
from lintro.ai.resolved_ai_config import ResolvedAIConfig
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
from lintro.ai.transport import ResolvedTransportSettings
from lintro.cli_utils.commands.review import _MetadataStamp, _stamp_metadata

_GLM = ReportedModel(model="glm-5.3-flash", canonical="glm-5.3-flash", provider="zai")

# --- flat codec ---------------------------------------------------------------


def test_provenance_fields_round_trip_through_the_flat_payload() -> None:
    """Every decision-B field survives serialize/parse, including a False."""
    record = RunRecord(
        identity=RunIdentity(
            round=2,
            endpoint="api.z.ai",
            first_party=False,
            credential="zai_token",
            reported_models=(_GLM,),
            unreported_models=("silent-model",),
            lane="zai",
            model_verification="unverifiable",
        ),
        usage=RunUsage(cost=0.0, cost_basis="unknown", cost_shadow=0.42),
    )

    payload = record.to_dict()
    restored = RunRecord.from_dict(payload)

    assert_that(payload["first_party"]).is_false()
    assert_that(payload["reported_models"]).is_equal_to(
        [{"model": "glm-5.3-flash", "provider": "zai"}],
    )
    assert_that(payload["cost_shadow"]).is_equal_to(0.42)
    assert_that(restored).is_equal_to(record)


@pytest.mark.parametrize("claims", [None, "silent-model", ["allowed", 3], [" "]])
def test_malformed_silent_claims_cannot_disappear_on_parse(claims: object) -> None:
    """Bad claim data retains a disallowed name instead of erasing the claim."""
    restored = RunRecord.from_dict(
        {"round": 1, "unreported_models": claims, "model_verification": "unreported"},
    )

    assert_that(restored.identity.unreported_models).is_not_empty()
    assert_that(
        any(not model.strip() for model in restored.identity.unreported_models),
    ).is_true()


def test_a_bare_name_in_a_stored_model_list_still_parses() -> None:
    """A list of names, as an earlier build wrote it, reads as models."""
    restored = RunRecord.from_dict(
        {"round": 1, "reported_models": ["claude-opus-5-5", 3, {"model": ""}]},
    )

    assert_that(restored.identity.reported_models).is_equal_to(
        (ReportedModel(model="claude-opus-5-5", canonical="claude-opus-5-5"),),
    )


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


@pytest.mark.parametrize("total", [0, 15])
def test_explicit_null_verification_reads_as_unreported(total: int) -> None:
    """Null is missing evidence, even when no token usage was recorded (#3010)."""
    restored = RunRecord.from_dict(
        {
            "round": 1,
            "endpoint": "api.anthropic.com",
            "model_verification": None,
            "total": total,
        },
    )

    assert_that(restored.identity.model_verification).is_equal_to(
        ModelVerification.UNREPORTED.value,
    )
    assert_that(restored.to_dict()["model_verification"]).is_equal_to("unreported")


@pytest.mark.parametrize("raw", ["bogus", 3, ["verified"]])
def test_an_unrecognised_state_is_kept_visible_not_blanked(raw: object) -> None:
    """A corrupt state must not read as "no state", which reuse would accept."""
    restored = RunRecord.from_dict({"round": 1, "model_verification": raw})

    state = restored.identity.model_verification
    assert_that(state).is_not_empty()
    assert_that(state).is_not_in(*(member.value for member in ModelVerification))
    assert_that(len(state)).is_less_than_or_equal_to(32)


def test_a_default_record_writes_no_provenance_keys() -> None:
    """A default record writes none of the new keys at all."""
    payload = RunRecord().to_dict()

    for key in ("endpoint", "first_party", "model_verification", "cost_shadow"):
        assert_that(payload).does_not_contain_key(key)


def test_first_party_is_written_whenever_it_was_recorded() -> None:
    """A lane row can say first-party without naming an endpoint (Cursor)."""
    payload = RunRecord(identity=RunIdentity(first_party=True)).to_dict()

    assert_that(payload["first_party"]).is_true()
    assert_that(RunRecord.from_dict(payload).identity.first_party).is_true()


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
    sonnet = ReportedModel(
        model="claude-sonnet-4-20250514",
        canonical="claude-sonnet-4-20250514",
        provider="firstParty",
    )
    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            endpoint="api.anthropic.com",
            first_party=True,
            credential="claude_oauth",
            reported_models=(sonnet,),
            unreported_models=("silent-model",),
            lane="anthropic",
            model_verification="verified",
        ),
    )

    identity = _record(result, cost_basis="list_price_shadow").identity

    assert_that(identity.endpoint).is_equal_to("api.anthropic.com")
    assert_that(identity.first_party).is_true()
    assert_that(identity.credential).is_equal_to("claude_oauth")
    assert_that(identity.reported_models).is_equal_to((sonnet,))
    assert_that(identity.unreported_models).is_equal_to(("silent-model",))
    assert_that(identity.lane).is_equal_to("anthropic")
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


# --- the post-run stamp ---------------------------------------------------------


def _stamp(basis: CostBasis, auth_mode: str = "subscription") -> _MetadataStamp:
    config = AIConfig(enabled=True, transport=AITransport.CLI)
    return _MetadataStamp(
        profile=ResolvedTransportSettings(
            transport=AITransport.CLI,
            timeout=60.0,
            max_cost_usd=None,
            cost_is_advisory=True,
            auth_mode=auth_mode,  # type: ignore[arg-type]
            cost_basis=basis,
        ),
        resolved_ai=ResolvedAIConfig(config=config, sources={}),
        cap=None,
        cap_source=ConfigSource.DEFAULT,
    )


def test_the_stamp_upgrades_a_reported_subscription_figure_to_a_shadow(
    sample_review_result: ReviewResult,
) -> None:
    """A first-party CLI run that reported a figure records ``list_price_shadow``."""
    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            first_party=True,
            credential="claude_oauth",
            cost_reported=True,
        ),
    )

    stamped = _stamp_metadata(result=result, stamp=_stamp(CostBasis.UNPRICEABLE))

    assert_that(stamped.metadata.cost_basis).is_equal_to("list_price_shadow")
    assert_that(stamped.metadata.auth_mode).is_equal_to("subscription")
    assert_that(stamped.metadata.cost_estimate_usd).is_equal_to(
        sample_review_result.metadata.cost_estimate_usd,
    )


def test_the_stamp_keeps_a_gateway_runs_in_run_shadow_figure(
    sample_review_result: ReviewResult,
) -> None:
    """Off the provider's host the calls were shadow-priced as they returned.

    The round's figure already includes charges the token totals cannot see
    (a dropped partial, a turn-limited call), so the stamp keeps it and the
    PR budget reads the same number (ADR-0011 B).
    """
    from lintro.ai.review.pr_budget import PrBudget

    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            first_party=False,
            credential="zai_token",
            cost_reported=True,
            # The in-run figure: $18 of shadow-priced calls plus a $2 charge
            # for a call whose partial was dropped.
            cost_estimate_usd=20.0,
            shadow_price_model="claude-sonnet-4-20250514",
            token_usage={
                "prompt": 1_000_000,
                "completion": 1_000_000,
                "total": 2_000_000,
            },
        ),
    )
    stamp = replace(
        _stamp(CostBasis.UNPRICEABLE),
        pr_budget=PrBudget(budget_usd=50.0, prior_spend_usd=5.0, enforced=True),
    )

    stamped = _stamp_metadata(result=result, stamp=stamp)

    assert_that(stamped.metadata.cost_basis).is_equal_to("unknown")
    assert_that(stamped.metadata.auth_mode).is_equal_to("gateway_token")
    assert_that(stamped.metadata.cost_estimate_usd).is_equal_to(20.0)
    assert_that(stamped.metadata.pr_budget_spent_usd).is_close_to(25.0, 1e-9)


def test_the_stamp_falls_back_to_the_profile_when_no_credential_was_recorded(
    sample_review_result: ReviewResult,
) -> None:
    """A run that made no call keeps the policy-derived auth mode."""
    stamped = _stamp_metadata(
        result=sample_review_result,
        stamp=_stamp(CostBasis.UNPRICEABLE, auth_mode="api_key"),
    )

    assert_that(stamped.metadata.auth_mode).is_equal_to("api_key")
    assert_that(stamped.metadata.cost_basis).is_equal_to("unpriceable")


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


def test_a_gateway_basis_is_enforced_by_repo_policy_but_labelled_a_shadow() -> None:
    """``unknown`` bills a gateway, so YAML enforces; its figure is still a shadow."""
    budget = resolve_pr_budget(
        budget_usd=10.0,
        source=ConfigSource.CONFIG,
        basis=CostBasis.UNKNOWN,
        prior_state=None,
    )

    assert budget is not None
    assert_that(budget.unpriceable).is_true()
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


def test_the_final_record_keeps_the_checkpoints_model_evidence(
    sample_review_result: ReviewResult,
) -> None:
    """A resumed round's record lists the models of the attempt it inherited from."""
    from lintro.ai.review.models.run_coverage import RunCoverage
    from lintro.ai.review.sticky.assembly import advance_review_state

    haiku = ReportedModel(model="claude-haiku-4-5", canonical="claude-haiku-4-5")
    prior = ReviewState(
        runs=(
            RunRecord(
                identity=RunIdentity(
                    round=1,
                    reported_models=(haiku,),
                    model_verification="verified",
                ),
                coverage=RunCoverage(partial=True, checkpoint=True),
            ),
        ),
    )
    result = replace(
        sample_review_result,
        metadata=replace(sample_review_result.metadata, reported_models=(_GLM,)),
    )

    advanced = advance_review_state(
        request=StickyRequest(result=result, prior_state=prior, head_sha="abc"),
    )

    assert_that([run.identity.round for run in advanced.runs]).is_equal_to([1])
    assert_that(advanced.runs[0].coverage.checkpoint).is_false()
    assert_that(
        [m.name for m in advanced.runs[0].identity.reported_models],
    ).is_equal_to(
        ["glm-5.3-flash", "claude-haiku-4-5"],
    )


def test_an_attempt_on_another_lane_leaves_no_evidence_on_the_record(
    sample_review_result: ReviewResult,
) -> None:
    """A verified checkpoint from a different lane was not inherited either."""
    from lintro.ai.review.models.run_coverage import RunCoverage
    from lintro.ai.review.sticky.assembly import advance_review_state

    other_lane = ReportedModel(model="gpt-5", canonical="gpt-5")
    prior = ReviewState(
        runs=(
            RunRecord(
                identity=RunIdentity(
                    round=1,
                    lane="openai",
                    endpoint="api.openai.com",
                    reported_models=(other_lane,),
                    model_verification="verified",
                ),
                coverage=RunCoverage(partial=True, checkpoint=True),
            ),
        ),
    )
    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            lane="anthropic",
            endpoint="api.anthropic.com",
            reported_models=(_GLM,),
            model_verification="verified",
        ),
    )

    advanced = advance_review_state(
        request=StickyRequest(result=result, prior_state=prior, head_sha="abc"),
    )

    assert_that(
        [m.name for m in advanced.runs[0].identity.reported_models],
    ).is_equal_to(
        ["glm-5.3-flash"],
    )
