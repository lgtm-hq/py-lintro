# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""A fallback lane is named on the record and leads the sticky (ADR-0011 D, #2872).

The wrapper exports which lane died and why before a fallback attempt; the
run detects it, the run record keeps it, and the sticky leads with it.
"""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.provenance import (
    REVIEW_FALLBACK_FROM_ENV,
    REVIEW_FALLBACK_REASON_ENV,
    REVIEW_LANE_ENV,
    detect_run_provenance,
)
from lintro.ai.registry import AIProvider
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.provenance_display import (
    ProvenanceView,
    fallback_banner,
    view_from_identity,
    view_from_metadata,
)
from lintro.ai.review.sticky.assembly import build_sticky_bodies, render_state_sticky


def test_the_run_reads_the_fallback_the_wrapper_exported(tmp_path: Path) -> None:
    """Both variables land on the run provenance; absent means no fallback."""
    with_fallback = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        env={
            "HOME": str(tmp_path),
            REVIEW_LANE_ENV: "openai",
            REVIEW_FALLBACK_FROM_ENV: " anthropic ",
            REVIEW_FALLBACK_REASON_ENV: "insufficient_credits",
        },
        home=tmp_path,
        cwd=str(tmp_path),
    )
    without = detect_run_provenance(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        env={"HOME": str(tmp_path)},
        home=tmp_path,
        cwd=str(tmp_path),
    )

    assert_that(with_fallback.fallback_from).is_equal_to("anthropic")
    assert_that(with_fallback.fallback_reason).is_equal_to("insufficient_credits")
    assert_that(without.fallback_from).is_empty()
    assert_that(without.fallback_reason).is_empty()


def test_the_fallback_round_trips_through_the_run_record() -> None:
    """The record keeps the fields, and omits them when there was no fallback."""
    record = RunRecord(
        identity=RunIdentity(
            round=3,
            lane="openai",
            fallback_from="anthropic",
            fallback_reason="auth_failed",
        ),
    )
    plain = RunRecord(identity=RunIdentity(round=1, lane="anthropic"))

    payload = record.to_dict()
    restored = RunRecord.from_dict(payload)

    assert_that(payload["fallback_from"]).is_equal_to("anthropic")
    assert_that(payload["fallback_reason"]).is_equal_to("auth_failed")
    assert_that(restored.identity.fallback_from).is_equal_to("anthropic")
    assert_that(restored.identity.fallback_reason).is_equal_to("auth_failed")
    assert_that(plain.to_dict()).does_not_contain_key("fallback_from")
    assert_that(RunRecord.from_dict(plain.to_dict()).identity.fallback_from).is_empty()


def test_the_final_record_keeps_the_fallback(
    sample_review_result: ReviewResult,
) -> None:
    """The record the run persists at the end carries what the checkpoints did.

    The final persist replaces every checkpoint part, so a board re-rendered
    from state alone would otherwise lose the fallback line.
    """
    from dataclasses import replace

    from lintro.ai.review.enums.review_verdict import ReviewVerdict
    from lintro.ai.review.run_record_factory import RoundTotals, run_record_from_result

    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            lane="openai",
            fallback_from="anthropic",
            fallback_reason="insufficient_credits",
        ),
    )

    record = run_record_from_result(
        request=StickyRequest(
            result=result,
            head_sha="abc1234",
            transport="cli",
            auth_mode="oauth",
        ),
        totals=RoundTotals(
            round_number=2,
            verdict=ReviewVerdict.READY,
            resolved=0,
            open_after=0,
            convergence_score=5.0,
        ),
    )
    restored = RunRecord.from_dict(record.to_dict())

    assert_that(record.identity.fallback_from).is_equal_to("anthropic")
    assert_that(record.identity.fallback_reason).is_equal_to("insufficient_credits")
    assert_that(restored.identity.fallback_from).is_equal_to("anthropic")
    assert_that(render_state_sticky(state=ReviewState(runs=(restored,)))).contains(
        "**Fallback lane**",
    )


def test_the_banner_names_both_lanes_and_the_reason() -> None:
    """The line says which lane died, why, and which lane answered."""
    view = ProvenanceView(
        lane="openai",
        fallback_from="anthropic",
        fallback_reason="insufficient_credits",
    )

    banner = fallback_banner(view)

    assert_that(banner).starts_with("> ⚠️ **Fallback lane**")
    assert_that(banner).contains("primary lane `anthropic` unavailable")
    assert_that(banner).contains("(`insufficient_credits`)")
    assert_that(banner).contains("reviewed by lane `openai`")
    assert_that(fallback_banner(ProvenanceView(lane="anthropic"))).is_empty()


def test_the_banner_sanitises_what_it_quotes() -> None:
    """A lane id or kind from the environment cannot inject markup or run long."""
    view = ProvenanceView(
        lane="openai",
        fallback_from="x" * 200 + "<script>",
        fallback_reason="kind\n::error::injected",
    )

    banner = fallback_banner(view)

    assert_that(banner).does_not_contain("<script>")
    assert_that(banner).does_not_contain("\n::error::")
    assert_that(len(banner)).is_less_than(300)


def test_the_sticky_leads_with_the_fallback_line(
    sample_review_result: ReviewResult,
) -> None:
    """On a fallback round the line sits under the title, before any fold."""
    from dataclasses import replace

    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            lane="openai",
            fallback_from="anthropic",
            fallback_reason="insufficient_credits",
        ),
    )

    primary, _archive = build_sticky_bodies(
        request=StickyRequest(result=result, prior_state=None, head_sha="abc1234"),
    )

    at = primary.index("**Fallback lane**")
    assert_that(at).is_less_than(primary.index("### "))
    first_fold = primary.find("<details>")
    if first_fold != -1:
        assert_that(at).is_less_than(first_fold)
    assert_that(view_from_metadata(result.metadata).fallback_from).is_equal_to(
        "anthropic",
    )


def test_a_state_only_board_keeps_the_last_rounds_fallback_line() -> None:
    """A board re-rendered from state reads the stored round's fallback."""
    prior = ReviewState(
        runs=(
            RunRecord(
                identity=RunIdentity(
                    round=2,
                    model="gpt-6.1-sol",
                    endpoint="api.openai.com",
                    first_party=True,
                    lane="openai",
                    model_verification="unreported",
                    fallback_from="anthropic",
                    fallback_reason="quota_exceeded",
                ),
            ),
        ),
    )

    body = render_state_sticky(state=prior)

    assert_that(body).contains("**Fallback lane**")
    assert_that(body).contains("`quota_exceeded`")
    assert_that(
        view_from_identity(prior.runs[-1].identity).fallback_reason,
    ).is_equal_to(
        "quota_exceeded",
    )
