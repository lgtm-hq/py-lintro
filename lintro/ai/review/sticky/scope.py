"""The sticky's one line on what a round read (#2627).

A delta round reads the change since the prior round's head; a full round
says why it did not. Rendered under the Findings heading by
:mod:`lintro.ai.review.sticky.findings`.
"""

from __future__ import annotations

from lintro.ai.review.enums.delta_reason import DeltaReason
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.sticky.cells import _short_sha

__all__ = ["_scope_line"]


_FULL_REASONS: dict[str, str] = {
    str(DeltaReason.NO_PRIOR_HEAD): "the prior round recorded no head commit",
    str(DeltaReason.NOT_ANCESTOR): "the branch was rewritten since the prior round",
    str(DeltaReason.NO_TREE): "the run had no repository to compute the range in",
    str(DeltaReason.EXPLICIT_FULL): "`--full` asked for the whole diff",
}


def _scope_line(*, runs: tuple[RunRecord, ...], round_number: int) -> str:
    """Say what this round read: the delta since the prior head, or why not.

    Silent on round one (there is nothing to be a delta of) and on a record
    written before delta rounds existed (#2627).

    Args:
        runs: Every recorded round, this one last.
        round_number: The round being rendered.

    Returns:
        One line, or an empty string.
    """
    if round_number <= 1 or not runs:
        return ""
    coverage = runs[-1].coverage
    if not coverage.delta_reason:
        return ""
    if coverage.delta_since:
        return (
            f"🔁 Round {round_number} read the delta since "
            f"`{_short_sha(sha=coverage.delta_since)}`; files with open threads "
            "were re-read."
        )
    why = _FULL_REASONS.get(coverage.delta_reason, coverage.delta_reason)
    return f"🔁 Round {round_number} read the whole diff: {why}."
