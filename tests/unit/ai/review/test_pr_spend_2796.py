"""The PR's review spend is cumulative and checkpointed (#2796, #2811 head 2).

Two ways the per-PR budget used to under-count, both from review threads on
#2811:

* a round killed after a mid-run checkpoint persisted its coverage but not its
  cost, so the next round saw more budget than was left;
* ``ReviewState`` keeps the newest 30 run records, and a sum over the
  survivors dropped the older rounds' spend.

``ReviewState.pr_spend_usd`` (schema v5) fixes both: it is written at every
checkpoint and at the final write, merged by maximum, and never decreases.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.github_constants import MAX_STORED_RUNS, STATE_VERSION
from lintro.ai.review.incremental_coverage import checkpoint_writer
from lintro.ai.review.lifecycle.state import (
    persist_review_state,
    resolve_prior_state,
)
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.pr_budget import resolve_pr_budget
from lintro.ai.review.resume import plan_resume
from lintro.ai.review.review_state_codec import decode_state, leftover_state_block
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.state_store import load_ci_state, union_states, write_state_part
from lintro.ai.review.sticky.assembly import advance_review_state


def _runs(*costs: float) -> tuple[RunRecord, ...]:
    """Return run records costing ``costs``.

    Args:
        *costs: One ``usage.cost`` per run.

    Returns:
        The runs.
    """
    return tuple(RunRecord(usage=RunUsage(cost=cost)) for cost in costs)


def _context() -> ReviewContext:
    """Return a one-file review context.

    Returns:
        The context.
    """
    return ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(
                path="src/app.py",
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=(
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1 +1,2 @@\n value = 1\n+value = 2\n"
        ),
    )


def _checkpoint(
    *,
    tmp_path: Path,
    prior: ReviewState | None,
    round_spend: float,
    force_full: bool = False,
) -> ReviewState:
    """Write one mid-run checkpoint, then load it as the next round would.

    Args:
        tmp_path: The state directory.
        prior: The state the round started from.
        round_spend: What the round had spent when the chunk finished.
        force_full: Whether the round was a ``--full`` review.

    Returns:
        The state the next round loads after this round is killed.
    """
    context = _context()
    writer = checkpoint_writer(
        resume=plan_resume(
            context=context,
            prior=None,
            extra_skips=[],
            groups=(("src/app.py",),),
            force_full=True,
        ),
        context=context,
        prior_state=prior,
        force_full=force_full,
        policy=resolve_sensitivity_policy(strictness=ReviewStrictness.BALANCED),
        round_spend=lambda: round_spend,
    )
    partial = ChunkReviewPartial(
        findings=(),
        input_tokens=0,
        output_tokens=0,
        cost_estimate=round_spend,
        files=("src/app.py",),
    )
    with patch.dict("os.environ", {"LINTRO_REVIEW_STATE_DIR": str(tmp_path)}):
        writer([partial])
    # The round is killed here: no final write. The next round loads parts.
    return load_ci_state(directory=tmp_path, repo="", pr_number=0)


def test_an_interrupted_round_still_counts_against_the_budget(tmp_path: Path) -> None:
    """Checkpoint after one chunk, kill the round, resume: the chunk is spent."""
    prior = ReviewState(runs=_runs(10.03, 7.63), pr_spend_usd=17.66)

    resumed = _checkpoint(tmp_path=tmp_path, prior=prior, round_spend=2.5)
    budget = resolve_pr_budget(
        budget_usd=40.0,
        source=ConfigSource.ENV,
        basis=CostBasis.UNPRICEABLE,
        prior_state=resumed,
    )

    assert_that(resumed.review_spend_usd).is_close_to(20.16, 1e-9)
    assert budget is not None
    assert_that(budget.remaining_usd).is_close_to(19.84, 1e-9)


def test_a_full_round_checkpoint_keeps_the_prs_spend(tmp_path: Path) -> None:
    """A ``--full`` checkpoint seeds from an empty state but not an empty total."""
    prior = ReviewState(runs=_runs(5.0), pr_spend_usd=5.0)

    resumed = _checkpoint(
        tmp_path=tmp_path,
        prior=prior,
        round_spend=1.0,
        force_full=True,
    )

    assert_that(resumed.review_spend_usd).is_close_to(6.0, 1e-9)


def test_a_first_round_checkpoint_records_its_own_spend(tmp_path: Path) -> None:
    """With no prior state the checkpoint total is the round's spend alone."""
    resumed = _checkpoint(tmp_path=tmp_path, prior=None, round_spend=0.75)

    assert_that(resumed.review_spend_usd).is_close_to(0.75, 1e-9)


def _result(cost: float) -> ReviewResult:
    """Return a minimal completed round costing ``cost``.

    Args:
        cost: The round's ``cost_estimate_usd``.

    Returns:
        The result.
    """
    return ReviewResult(
        summary="",
        metadata=ReviewMetadata(
            model="m",
            provider="p",
            context_window=200_000,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            chunks_reviewed=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=0,
            cost_estimate_usd=cost,
        ),
    )


def test_pruned_run_history_never_drops_spend() -> None:
    """Past 30 rounds the runs are pruned; the budget still sees every dollar."""
    rounds = MAX_STORED_RUNS + 5
    state = ReviewState()
    for _ in range(rounds):
        state = advance_review_state(
            request=StickyRequest(result=_result(1.0), prior_state=state),
        )
    budget = resolve_pr_budget(
        budget_usd=40.0,
        source=ConfigSource.ENV,
        basis=CostBasis.UNPRICEABLE,
        prior_state=state,
    )

    assert_that(state.runs).is_length(MAX_STORED_RUNS)
    assert_that(state.truncated).is_true()
    assert_that(sum(run.usage.cost for run in state.runs)).is_close_to(30.0, 1e-9)
    assert_that(state.review_spend_usd).is_close_to(float(rounds), 1e-9)
    assert budget is not None
    assert_that(budget.remaining_usd).is_close_to(40.0 - rounds, 1e-9)


def test_the_final_write_adds_the_round_to_a_checkpointed_total() -> None:
    """A resumed round adds its own cost on top of the interrupted spend."""
    resumed = ReviewState(runs=_runs(4.0), pr_spend_usd=6.5)

    advanced = advance_review_state(
        request=StickyRequest(result=_result(1.25), prior_state=resumed),
    )

    assert_that(advanced.review_spend_usd).is_close_to(7.75, 1e-9)


def test_the_total_round_trips_through_the_artifact() -> None:
    """``pr_spend_usd`` is written and read back at the current schema."""
    state = ReviewState(runs=_runs(1.0), pr_spend_usd=42.5)

    payload = json.loads(json.dumps(state.to_artifact_dict()))
    restored = ReviewState.from_artifact_dict(payload)

    assert_that(payload["schema_version"]).is_equal_to(STATE_VERSION)
    assert_that(STATE_VERSION).is_greater_than_or_equal_to(5)
    assert_that(restored.pr_spend_usd).is_equal_to(42.5)


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param(None, id="absent-v4"),
        pytest.param(-3.0, id="negative"),
        pytest.param("lots", id="text"),
        pytest.param(True, id="bool"),
    ],
)
def test_an_old_or_bad_total_seeds_from_the_surviving_runs(stored: object) -> None:
    """A v4 artifact (or a garbled total) counts at least its retained runs.

    Args:
        stored: The ``pr_spend_usd`` value in the payload, or None to omit it.
    """
    payload = ReviewState(runs=_runs(2.0, 3.0)).to_artifact_dict()
    payload["schema_version"] = payload["version"] = 4
    del payload["pr_spend_usd"]
    if stored is not None:
        payload["pr_spend_usd"] = stored

    restored = ReviewState.from_artifact_dict(payload)

    assert_that(restored.pr_spend_usd).is_equal_to(0.0)
    assert_that(restored.review_spend_usd).is_close_to(5.0, 1e-9)


@pytest.mark.parametrize(
    ("checkpointed", "expected"),
    [
        pytest.param(9.0, 9.0, id="checkpoint-higher-kept"),
        pytest.param(5.5, 6.0, id="final-higher-wins"),
    ],
)
def test_the_final_write_never_lowers_the_checkpointed_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpointed: float,
    expected: float,
) -> None:
    """A charged-then-cancelled call stays counted (ruling 17 (a)).

    The round's checkpoint recorded more spend than the completed result
    carries (a parallel chunk was charged, then cancelled at the budget). The
    final write keeps the higher on-disk total instead of replacing it.

    Args:
        tmp_path: Scratch directory holding the state directory.
        monkeypatch: Pytest monkeypatch fixture.
        checkpointed: The total this round's checkpoint wrote.
        expected: The total the final write must persist.
    """
    state_dir = tmp_path / "state"
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(state_dir))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    prior = ReviewState(runs=_runs(5.0), pr_spend_usd=5.0)
    write_state_part(
        state=replace(prior, repo="o/r", pr_number=2811, pr_spend_usd=checkpointed),
        directory=state_dir,
        sequence=2,
        final=True,
    )

    persist_review_state(
        result=_result(1.0),
        context=_context(),
        prior=prior,
        pr_number=2811,
        repo="o/r",
    )
    written = load_ci_state(directory=state_dir, repo="o/r", pr_number=2811)

    assert_that(written.pr_spend_usd).is_close_to(expected, 1e-9)


def test_another_prs_parts_never_raise_the_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only this PR's parts are read back; a stray part is ignored.

    Args:
        tmp_path: Scratch directory holding the state directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    state_dir = tmp_path / "state"
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(state_dir))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    write_state_part(
        state=ReviewState(repo="o/r", pr_number=9999, pr_spend_usd=500.0),
        directory=state_dir,
        sequence=2,
    )

    persist_review_state(
        result=_result(1.0),
        context=_context(),
        prior=ReviewState(runs=_runs(5.0), pr_spend_usd=5.0),
        pr_number=2811,
        repo="o/r",
    )
    written = load_ci_state(directory=state_dir, repo="o/r", pr_number=2811)

    assert_that(written.pr_spend_usd).is_close_to(6.0, 1e-9)


def test_a_local_run_never_reads_ci_artifacts_for_spend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside Actions the checkpoint read-back is skipped (#2154 boundary).

    Args:
        tmp_path: Scratch directory holding the state directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    state_dir = tmp_path / "state"
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(state_dir))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)
    write_state_part(
        state=ReviewState(repo="o/r", pr_number=2811, pr_spend_usd=500.0),
        directory=state_dir,
        sequence=2,
    )

    persist_review_state(
        result=_result(1.0),
        context=_context(),
        prior=ReviewState(runs=_runs(5.0), pr_spend_usd=5.0),
        pr_number=2811,
        repo="o/r",
    )
    written = load_ci_state(directory=state_dir, repo="o/r", pr_number=2811)

    assert_that(written.pr_spend_usd).is_close_to(6.0, 1e-9)


def test_the_sticky_fallback_keeps_the_cumulative_total() -> None:
    """A state recovered from a sticky blob does not re-seed from its runs.

    ``resolve_prior_state`` falls back to the sticky comment's own state when
    no artifact is found (ruling 17 (b)).
    """
    state = ReviewState(runs=_runs(1.0, 2.0), pr_spend_usd=48.0)

    decoded = decode_state(body=leftover_state_block(state=state))
    resolved = resolve_prior_state(prior_state=None, sticky_state=decoded)

    assert_that(resolved.review_spend_usd).is_close_to(48.0, 1e-9)


def test_a_sticky_blob_without_a_total_seeds_from_its_runs() -> None:
    """An older blob (no ``pr_spend_usd``) still counts the runs it kept."""
    block = leftover_state_block(state=ReviewState(runs=_runs(1.0, 2.0)))
    old_block = block.replace(',"pr_spend_usd":3.0', "")
    assert_that(old_block).is_not_equal_to(block)

    decoded = decode_state(body=old_block)

    assert_that(decoded.pr_spend_usd).is_equal_to(0.0)
    assert_that(decoded.review_spend_usd).is_close_to(3.0, 1e-9)


def test_merging_parts_keeps_the_largest_total() -> None:
    """A later part never lowers the spend an earlier part recorded."""
    early = ReviewState(runs=_runs(1.0), pr_spend_usd=9.0)
    late = replace(early, pr_spend_usd=4.0)

    assert_that(union_states([early, late]).pr_spend_usd).is_equal_to(9.0)
    assert_that(union_states([late, early]).pr_spend_usd).is_equal_to(9.0)
