"""A mid-run checkpoint records the round's provenance and basis (#2867).

ADR-0011 decision B: a round killed after a checkpoint leaves coverage
behind; the partial run record written with it says which lane and endpoint
produced that coverage, so the reuse gate can judge it, and a gateway's
figure is checkpointed as a shadow, never as spend.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.provenance import LedgerCall, RunProvenance
from lintro.ai.providers.reported_model import ReportedModel
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.incremental_coverage import (
    CheckpointStamp,
    write_incremental_coverage_part,
)
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.resume import plan_resume
from lintro.ai.review.reuse_gate import reusable_rounds
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.state_store import load_ci_state

_REPO = "lgtm-hq/py-lintro"
_PR = 2886
_IDENTITY = RunIdentity(
    model="claude-opus-5-5",
    provider="anthropic",
    transport="cli",
    endpoint="api.anthropic.com",
    first_party=True,
    credential="claude_oauth",
    lane="claude-subscription",
)


def _context() -> ReviewContext:
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff="diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n x\n+y\n",
        pr_metadata=None,
    )


def _partial(*, input_tokens: int, output_tokens: int) -> ChunkReviewPartial:
    return ChunkReviewPartial(
        findings=(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=0.0,
        files=(),
    )


def _checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    prior: ReviewState | None,
    stamp: CheckpointStamp,
    collected: list[ChunkReviewPartial] | None = None,
) -> ReviewState:
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    context = _context()
    write_incremental_coverage_part(
        collected=collected or [],
        resume=plan_resume(context=context, prior=prior),
        context=context,
        prior_state=prior,
        force_full=False,
        sequence=1,
        policy=resolve_sensitivity_policy(strictness=ReviewStrictness.BALANCED),
        stamp=stamp,
    )
    return load_ci_state(directory=tmp_path, repo=_REPO, pr_number=_PR)


def test_a_checkpoint_writes_a_partial_run_with_the_rounds_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The partial record carries lane and endpoint, no state, and is incomplete."""
    loaded = _checkpoint(
        tmp_path,
        monkeypatch,
        prior=None,
        stamp=CheckpointStamp(
            pr_number=_PR,
            round_spend_usd=0.3,
            identity=_IDENTITY,
            cost_basis="unpriceable",
        ),
    )

    assert_that(loaded.runs).is_length(1)
    partial = loaded.runs[0]
    assert_that(partial.identity.round).is_equal_to(1)
    assert_that(partial.identity.lane).is_equal_to("claude-subscription")
    assert_that(partial.identity.endpoint).is_equal_to("api.anthropic.com")
    assert_that(partial.identity.model_verification).is_empty()
    assert_that(partial.coverage.partial).is_true()
    assert_that(partial.outcome.verdict).is_equal_to(ReviewVerdict.INCOMPLETE)
    assert_that(partial.usage.cost).is_equal_to(0.3)
    assert_that(loaded.review_spend_usd).is_close_to(0.3, 1e-9)
    assert_that(loaded.review_shadow_usd).is_equal_to(0.0)


def test_a_checkpoint_carries_the_verification_of_the_calls_so_far(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A round killed after a mismatch is excluded from reuse like a finished one."""
    mismatch = LedgerCall(
        requested="claude-opus-5-5",
        reported=(ReportedModel(model="glm-5", canonical="glm-5"),),
    )
    loaded = _checkpoint(
        tmp_path,
        monkeypatch,
        prior=None,
        stamp=CheckpointStamp(
            pr_number=_PR,
            identity=_IDENTITY,
            cost_basis="unpriceable",
            verify=lambda: ("mismatch", mismatch.reported),
        ),
    )

    partial = loaded.runs[0]
    assert_that(partial.identity.model_verification).is_equal_to("mismatch")
    assert_that([m.name for m in partial.identity.reported_models]).is_equal_to(
        ["glm-5"],
    )
    assert_that(
        reusable_rounds(
            prior=loaded,
            provenance=RunProvenance(
                endpoint="api.anthropic.com",
                first_party=True,
                lane="claude-subscription",
            ),
            allowed=("claude-opus-5-5",),
        ),
    ).is_empty()


def test_a_checkpoint_record_does_not_advance_the_round_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run that resumes a killed round keeps its number (ADR-0007)."""
    loaded = _checkpoint(
        tmp_path,
        monkeypatch,
        prior=None,
        stamp=CheckpointStamp(pr_number=_PR, identity=_IDENTITY),
    )

    assert_that(loaded.runs[0].coverage.checkpoint).is_true()
    assert_that(loaded.runs[0].to_dict()["checkpoint"]).is_true()
    assert_that(loaded.next_round).is_equal_to(1)


def test_a_gateway_checkpoint_prices_its_shadow_on_tokens_not_the_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On ``unknown`` the checkpoint discards the envelope figure (ADR-0011 B)."""
    loaded = _checkpoint(
        tmp_path,
        monkeypatch,
        prior=None,
        stamp=CheckpointStamp(
            pr_number=_PR,
            round_spend_usd=99.0,
            identity=_IDENTITY,
            cost_basis="unknown",
            shadow_price_model="claude-sonnet-4-20250514",
        ),
        collected=[_partial(input_tokens=1_000_000, output_tokens=1_000_000)],
    )

    # Sonnet's list table: $3 per million in, $15 per million out.
    assert_that(loaded.runs[0].usage.cost_shadow).is_close_to(18.0, 1e-9)
    assert_that(loaded.review_shadow_usd).is_close_to(18.0, 1e-9)
    assert_that(loaded.review_spend_usd).is_equal_to(0.0)


def test_a_gateway_checkpoint_records_its_figure_as_a_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On ``unknown`` the checkpointed figure is a shadow, on top of the prior."""
    prior = ReviewState(
        runs=(
            RunRecord(
                identity=RunIdentity(round=1, endpoint="api.z.ai", lane="zai"),
                usage=RunUsage(cost_basis="unknown", cost_shadow=1.0),
            ),
        ),
        repo=_REPO,
        pr_number=_PR,
        pr_spend_usd=0.5,
    )

    loaded = _checkpoint(
        tmp_path,
        monkeypatch,
        prior=prior,
        stamp=CheckpointStamp(
            pr_number=_PR,
            round_spend_usd=0.25,
            identity=_IDENTITY,
            cost_basis="unknown",
            shadow_price_model="claude-sonnet-4-20250514",
        ),
        collected=[_partial(input_tokens=1_000_000, output_tokens=0)],
    )

    assert_that([run.identity.round for run in loaded.runs]).is_equal_to([1, 2])
    assert_that(loaded.runs[1].usage.cost).is_equal_to(0.0)
    assert_that(loaded.runs[1].usage.cost_shadow).is_close_to(3.0, 1e-9)
    assert_that(loaded.review_spend_usd).is_equal_to(0.5)
    assert_that(loaded.review_shadow_usd).is_close_to(4.0, 1e-9)


def test_a_checkpoint_without_an_identity_writes_no_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-#2867 shape is unchanged when no identity is stamped."""
    loaded = _checkpoint(
        tmp_path,
        monkeypatch,
        prior=None,
        stamp=CheckpointStamp(pr_number=_PR, round_spend_usd=0.1),
    )

    assert_that(loaded.runs).is_empty()
    assert_that(loaded.review_spend_usd).is_close_to(0.1, 1e-9)
