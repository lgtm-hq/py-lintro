"""The sticky, badges, history and terminal name the endpoint and basis (#2868).

ADR-0011 decision B: no renderer prints the configured model without the
endpoint host and verification state beside it, and every dollar figure
carries its basis. A gateway round shows no dollars.
"""

from __future__ import annotations

from dataclasses import replace

from assertpy import assert_that

from lintro.ai.providers.reported_model import ReportedModel
from lintro.ai.review.github_badges import run_stats_primary_cells
from lintro.ai.review.github_notes import format_run_mechanics
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.provenance_display import (
    GATEWAY_COST_LABEL,
    LEGACY_LABEL,
    ProvenanceView,
    cost_label,
    endpoint_note,
    history_cost_summary,
    model_label,
    provenance_banner,
    terminal_cost_info,
    view_from_identity,
)
from lintro.ai.review.sticky.assembly import build_sticky_bodies

_VERIFIED = ProvenanceView(
    model="claude-opus-5-5",
    model_source="env",
    endpoint="api.anthropic.com",
    first_party=True,
    lane="claude-subscription",
    verification="verified",
)
_GATEWAY = replace(
    _VERIFIED,
    endpoint="api.z.ai",
    first_party=False,
    lane="zai",
    verification="unverifiable",
)


def test_the_model_never_stands_without_its_endpoint() -> None:
    """The configured model is always followed by where it went."""
    assert_that(model_label(_VERIFIED)).is_equal_to(
        "`claude-opus-5-5` (env) · verified on `api.anthropic.com`",
    )
    assert_that(model_label(_GATEWAY)).is_equal_to(
        "`claude-opus-5-5` (env) · answered via `api.z.ai` (third-party gateway), "
        "model unverifiable",
    )
    assert_that(model_label(_VERIFIED, plain=True)).does_not_contain("`")


def test_each_verification_state_reads_as_itself() -> None:
    """Mismatch names what answered; unreported is not red; legacy is labelled."""
    mismatch = replace(_VERIFIED, verification="mismatch", reported=("glm-4.6",))
    assert_that(endpoint_note(mismatch)).is_equal_to(
        "mismatch on `api.anthropic.com` (reported `glm-4.6`)",
    )
    unreported = replace(_VERIFIED, verification="unreported")
    assert_that(endpoint_note(unreported)).is_equal_to(
        "unverified by the transport on `api.anthropic.com`",
    )
    legacy = view_from_identity(RunIdentity(round=1, model="claude-opus-5-5"))
    assert_that(endpoint_note(legacy)).is_equal_to(LEGACY_LABEL)
    assert_that(provenance_banner(legacy)).contains(LEGACY_LABEL)
    # A live round that recorded nothing is not a stored pre-ADR record.
    blank = ProvenanceView(model="claude-opus-5-5")
    assert_that(endpoint_note(blank)).is_empty()
    assert_that(provenance_banner(blank)).is_empty()


def test_only_a_verified_round_leads_without_a_banner() -> None:
    """The banner names the lane, the endpoint and the state; verified has none."""
    assert_that(provenance_banner(_VERIFIED)).is_empty()
    banner = provenance_banner(_GATEWAY)
    assert_that(banner).starts_with("> ⚠️ **Model unverifiable**")
    assert_that(banner).contains("lane `zai`")
    assert_that(banner).contains("`api.z.ai` (third-party gateway)")
    mismatch = provenance_banner(replace(_VERIFIED, verification="mismatch"))
    assert_that(mismatch).starts_with("> ❌ **Model mismatch**")
    unreported = provenance_banner(replace(_VERIFIED, verification="unreported"))
    assert_that(unreported).starts_with("> ℹ️")


def test_every_figure_carries_its_basis_and_a_gateway_shows_no_dollars() -> None:
    """A dollar figure names what it is; unknown is n/a, never zero."""
    assert_that(cost_label(cost=1.5, basis="billed", estimated=False)).is_equal_to(
        "$1.5000 (billed)",
    )
    assert_that(cost_label(cost=1.5, basis="estimated", estimated=True)).is_equal_to(
        "~$1.5000 (estimated)",
    )
    assert_that(
        cost_label(cost=1.5, basis="list_price_shadow", estimated=True),
    ).is_equal_to("~$1.5000 (list-price shadow, billed to subscription)")
    assert_that(cost_label(cost=0.0, basis="unknown", estimated=True)).is_equal_to(
        GATEWAY_COST_LABEL,
    )
    assert_that(cost_label(cost=1.5, basis="unknown", estimated=True)).does_not_contain(
        "$",
    )
    # A legacy figure renders as it always did.
    assert_that(cost_label(cost=1.5, basis="", estimated=True)).is_equal_to("~$1.5000")


def _run(
    round_number: int,
    *,
    basis: str,
    cost: float = 0.0,
    shadow: float = 0.0,
) -> RunRecord:
    return RunRecord(
        identity=RunIdentity(round=round_number, model="m"),
        usage=RunUsage(cost=cost, cost_shadow=shadow, cost_basis=basis, estimated=True),
    )


def test_the_history_heading_discloses_rounds_the_gateway_left_unpriced() -> None:
    """A cumulative total never looks complete over unpriced rounds."""
    runs = (
        _run(1, basis="estimated", cost=0.5),
        _run(2, basis="unknown", shadow=0.7),
        _run(3, basis="list_price_shadow", shadow=0.2),
    )

    assert_that(history_cost_summary(runs)).is_equal_to(
        "~$0.5000 · $0.2000 shadow · 1 round unpriced (gateway)",
    )
    assert_that(history_cost_summary(runs[:1])).is_equal_to("~$0.5000")


def _gateway_result(sample_review_result: ReviewResult) -> ReviewResult:
    return replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            model="claude-opus-5-5",
            model_source="env",
            endpoint="api.z.ai",
            first_party=False,
            lane="zai",
            model_verification="unverifiable",
            cost_basis="unknown",
            cost_estimate_usd=0.42,
            reported_models=(ReportedModel(model="glm-4.6", canonical="glm-4.6"),),
        ),
    )


def test_the_badges_and_mechanics_follow_the_rule(
    sample_review_result: ReviewResult,
) -> None:
    """Every surface that prints the model prints the endpoint and the basis."""
    result = _gateway_result(sample_review_result)

    cells = dict(run_stats_primary_cells(metadata=result.metadata))
    mechanics = format_run_mechanics(metadata=result.metadata)

    assert_that(cells["model"]).contains("`api.z.ai` (third-party gateway)")
    assert_that(cells["est. cost"]).is_equal_to(GATEWAY_COST_LABEL)
    assert_that(mechanics).contains("**Lane:** `zai`")
    assert_that(mechanics).contains("**Est. cost:** " + GATEWAY_COST_LABEL)
    assert_that(mechanics).does_not_contain("$0.42")


def test_the_sticky_leads_with_the_banner_outside_every_fold(
    sample_review_result: ReviewResult,
) -> None:
    """The banner sits under the title, before any summary or collapsible."""
    result = _gateway_result(sample_review_result)

    primary, _archive = build_sticky_bodies(
        request=StickyRequest(result=result, prior_state=None, head_sha="abc1234"),
    )

    banner_at = primary.index("**Model unverifiable**")
    assert_that(banner_at).is_less_than(primary.index("### "))
    first_fold = primary.find("<details>")
    if first_fold != -1:
        assert_that(banner_at).is_less_than(first_fold)
    assert_that(primary).contains("`api.z.ai` (third-party gateway)")
    assert_that(primary).contains(GATEWAY_COST_LABEL)
    assert_that(primary).does_not_contain("$0.4200")


def test_a_state_only_board_names_the_last_rounds_provenance(
    sample_review_result: ReviewResult,
) -> None:
    """A board re-rendered from state reads the stored round's identity."""
    from lintro.ai.review.sticky.assembly import render_state_sticky

    prior = ReviewState(
        runs=(
            RunRecord(
                identity=RunIdentity(
                    round=1,
                    model="claude-opus-5-5",
                    endpoint="api.z.ai",
                    first_party=False,
                    lane="zai",
                    model_verification="unverifiable",
                ),
            ),
        ),
    )

    body = render_state_sticky(state=prior)

    assert_that(body).contains("**Model unverifiable**")


def test_the_terminal_header_carries_the_basis_and_no_gateway_dollars(
    sample_review_result: ReviewResult,
) -> None:
    """The terminal reads the same facts as the sticky."""
    result = _gateway_result(sample_review_result)

    info = terminal_cost_info(result.metadata)
    plain = model_label(
        ProvenanceView(
            model=result.metadata.model,
            endpoint=result.metadata.endpoint,
            first_party=False,
            verification="unverifiable",
        ),
        plain=True,
    )

    assert_that(info).contains(GATEWAY_COST_LABEL)
    assert_that(info).does_not_contain("$")
    assert_that(plain).is_equal_to(
        "claude-opus-5-5 · answered via api.z.ai (third-party gateway), "
        "model unverifiable",
    )
