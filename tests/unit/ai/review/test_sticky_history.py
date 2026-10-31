"""Unknown and measured fix counts in sticky run history (#3014)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_outcome import RunOutcome
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.sticky.history import _history_row, _round_expander


@pytest.mark.parametrize(
    ("resolved", "expected"),
    [(None, "—"), (0, "0"), (2, "2")],
    ids=["legacy-unknown", "measured-zero", "measured-positive"],
)
def test_history_preserves_unknown_and_measured_fix_counts(
    resolved: int | None,
    expected: str,
) -> None:
    """The round summary and table distinguish unmeasured counts from zero."""
    run = RunRecord(
        identity=RunIdentity(round=1),
        outcome=RunOutcome(resolved=resolved, open_after=3),
    )

    expander = _round_expander(run=run, records=())
    row = _history_row(run=run, latest=False)

    assert_that(expander).contains(f" · {expected} fixed, 3 left open · ")
    assert_that(row).contains(f"| 3 | {expected} |")
