# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""The persisted state names the run attempt that wrote it (#2871).

A rerun resumes its own cancelled attempt's artifact, which carries the same
run id, so the CI classifier keys this round's provenance on run id and
attempt together; the attempt must round-trip through the artifact dict.
"""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.state_store import union_states, write_state_part


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


def test_the_checkpoint_stamps_the_attempt_from_the_environment() -> None:
    """The writer reads ``GITHUB_RUN_ATTEMPT`` next to ``GITHUB_RUN_ID``."""
    source = Path("lintro/ai/review/incremental_coverage.py").read_text(
        encoding="utf-8",
    )
    assert_that(source).contains(
        'run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", "") or seed.run_attempt',
    )
