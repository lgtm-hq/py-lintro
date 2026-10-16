# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""The persisted state names the run attempt that wrote it (#2871).

A rerun resumes its own cancelled attempt's artifact, which carries the same
run id, so the CI classifier keys this round's provenance on run id and
attempt together; the attempt must round-trip through the artifact dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.lifecycle.state import persist_review_state
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.state_store import load_ci_state, union_states, write_state_part
from tests.unit.ai.review.test_pr_spend_2796 import _checkpoint, _context, _result


def test_the_run_attempt_round_trips_through_the_artifact_dict() -> None:
    """``run_attempt`` is written beside ``run_id`` and read back."""
    state = ReviewState(run_id="38900000001", run_attempt="2")

    payload = state.to_artifact_dict()
    restored = ReviewState.from_artifact_dict(payload)

    assert_that(payload["run_id"]).is_equal_to("38900000001")
    assert_that(payload["run_attempt"]).is_equal_to("2")
    assert_that(restored.run_attempt).is_equal_to("2")
    assert_that(ReviewState.from_artifact_dict({"version": 1}).run_attempt).is_empty()


def test_the_union_keeps_the_last_parts_attempt(tmp_path: Path) -> None:
    """Merging parts carries the attempt of the part that wrote last."""
    first = ReviewState(run_id="1", run_attempt="1")
    second = ReviewState(run_id="1", run_attempt="2")

    merged = union_states([first, second])

    assert_that(merged.run_attempt).is_equal_to("2")
    write_state_part(state=merged, directory=tmp_path, sequence=1, final=True)
    written = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert_that(written).contains('"run_attempt": "2"')


def test_the_checkpoint_writer_stamps_this_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-round checkpoint names the run and attempt, like the final write.

    A round killed after a checkpoint leaves only parts behind; the next
    load, and the CI classifier, must see this attempt's stamp on them.

    Args:
        tmp_path: Scratch state directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "38900000001")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")

    resumed = _checkpoint(tmp_path=tmp_path, prior=None, round_spend=0.5)

    assert_that(resumed.run_id).is_equal_to("38900000001")
    assert_that(resumed.run_attempt).is_equal_to("2")


def test_the_final_persist_stamps_this_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round's final write names the run and the attempt that wrote it.

    The CI classifier refuses a state naming another attempt, so a final
    write that stamped only the run id would redden every completed review
    on a rerun. Driven through the real persist path in Actions mode.

    Args:
        tmp_path: Scratch directory holding the state directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    state_dir = tmp_path / "state"
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(state_dir))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "38900000001")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    # A prior attempt's artifact is what the workflow downloads here.
    write_state_part(
        state=ReviewState(
            repo="o/r",
            pr_number=2871,
            run_id="38900000001",
            run_attempt="1",
        ),
        directory=state_dir,
        sequence=2,
        final=True,
    )

    persist_review_state(
        result=_result(1.0),
        context=_context(),
        prior=None,
        pr_number=2871,
        repo="o/r",
    )

    written = load_ci_state(directory=state_dir, repo="o/r", pr_number=2871)
    assert_that(written.run_id).is_equal_to("38900000001")
    assert_that(written.run_attempt).is_equal_to("2")
    raw = (state_dir / "state.json").read_text(encoding="utf-8")
    assert_that(raw).contains('"run_attempt": "2"')
