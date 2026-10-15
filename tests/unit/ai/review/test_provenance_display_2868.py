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
    UNRECORDED_ENDPOINT,
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
        "`claude-opus-5-5` (env) · verified on `api.anthropic.com` "
        "· lane `claude-subscription`",
    )
    assert_that(model_label(_GATEWAY)).is_equal_to(
        "`claude-opus-5-5` (env) · answered via `api.z.ai` (third-party gateway), "
        "model unverifiable · lane `zai`",
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
    legacy = view_from_identity(
        RunIdentity(round=1, model="claude-opus-5-5", model_verification="legacy"),
    )
    assert_that(endpoint_note(legacy)).is_equal_to(LEGACY_LABEL)
    # Legacy is a history-row label (ADR-0011 C); it never tops a board.
    assert_that(provenance_banner(legacy)).is_empty()
    # A round that recorded no endpoint says so; the model never stands alone.
    blank = ProvenanceView(model="claude-opus-5-5")
    assert_that(endpoint_note(blank)).is_equal_to(UNRECORDED_ENDPOINT)
    assert_that(model_label(blank)).is_equal_to(
        f"`claude-opus-5-5` · {UNRECORDED_ENDPOINT}",
    )
    assert_that(provenance_banner(blank)).is_empty()
    no_host = replace(_VERIFIED, endpoint="", first_party=None)
    assert_that(endpoint_note(no_host)).is_equal_to(
        f"verified on {UNRECORDED_ENDPOINT}",
    )
    assert_that(endpoint_note(no_host)).does_not_contain("``")


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
        "~$0.5000 (estimated) · $0.2000 shadow · 1 round unpriced (gateway)",
    )
    assert_that(history_cost_summary(runs[:1])).is_equal_to("~$0.5000 (estimated)")
    billed = (_run(1, basis="billed", cost=0.5), _run(2, basis="billed", cost=0.25))
    assert_that(history_cost_summary(billed)).is_equal_to("$0.7500 (billed)")
    # A history with no priced round shows no dollars at all.
    gateway_only = (_run(1, basis="unknown", shadow=0.3), _run(2, basis="unknown"))
    assert_that(history_cost_summary(gateway_only)).is_equal_to(
        f"{GATEWAY_COST_LABEL} · 2 rounds unpriced (gateway)",
    )
    assert_that(history_cost_summary(gateway_only)).does_not_contain("$")


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
    assert_that(cells["model"]).contains("lane `zai`")
    assert_that(cells["est. cost"]).is_equal_to(GATEWAY_COST_LABEL)
    assert_that(mechanics).contains("lane `zai`")
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


def test_a_first_round_error_comment_leads_with_the_banner(
    sample_review_result: ReviewResult,
) -> None:
    """The error-only body is the sticky, so the model claim leads it too."""
    from lintro.ai.exceptions import AIProviderError
    from lintro.ai.review.github_errors import format_error_comment

    result = _gateway_result(sample_review_result)

    body = format_error_comment(
        error=AIProviderError("boom"),
        provider="anthropic",
        metadata=result.metadata,
    )

    banner_at = body.index("**Model unverifiable**")
    assert_that(banner_at).is_less_than(body.index("<sub>"))
    assert_that(body).contains(GATEWAY_COST_LABEL)


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


def test_a_zero_figure_with_a_basis_keeps_its_basis_in_the_terminal(
    sample_review_result: ReviewResult,
) -> None:
    """A billed zero is not an estimate below a tenth of a cent."""
    metadata = replace(
        sample_review_result.metadata,
        cost_basis="billed",
        cost_estimate_usd=0.0,
        token_usage={"prompt": 10, "completion": 5, "total": 15},
    )

    assert_that(terminal_cost_info(metadata)).ends_with("$0.0000 (billed)")


def test_the_pr_budget_line_names_the_shadow_an_earlier_round_left_in_the_total(
    sample_review_result: ReviewResult,
) -> None:
    """A billed round after a gateway round still discloses the shadow it carries."""
    from lintro.ai.review.sticky.history import _pr_budget_line

    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            cost_basis="billed",
            cost_estimate_usd=0.10,
            pr_budget_usd=2.0,
            pr_budget_spent_usd=0.60,
            pr_budget_enforced=True,
        ),
    )
    runs = (
        _run(1, basis="unknown", shadow=0.5),
        _run(2, basis="billed", cost=0.10),
    )

    line = _pr_budget_line(result, runs=runs)

    assert_that(line).starts_with("PR budget: $0.60 of $2.00")
    assert_that(line).contains("includes $0.50 of shadow figures")
