"""Versioned review state persisted in the sticky comment's hidden blob."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.run_record import RunRecord

__all__ = ["ReviewState"]


@dataclass(frozen=True, slots=True)
class ReviewState:
    """Machine-readable review history for one pull request.

    Attributes:
        version: Schema version of the decoded blob.
        runs: Per-round statistics, oldest first.
        findings: Tracked findings (open and resolved) across all rounds.
        truncated: True when older runs or resolved findings were pruned to
            keep the sticky comment under GitHub's size limit.
    """

    version: int = 2
    runs: tuple[RunRecord, ...] = field(default_factory=tuple)
    findings: tuple[FindingRecord, ...] = field(default_factory=tuple)
    truncated: bool = False

    @property
    def next_round(self) -> int:
        """Return the round number the next review run should record."""
        if not self.runs:
            return 1
        return max(run.round for run in self.runs) + 1

    @property
    def open_findings(self) -> tuple[FindingRecord, ...]:
        """Return the currently open findings, oldest round first."""
        return tuple(
            record for record in self.findings if record.status is FindingStatus.OPEN
        )

    @property
    def resolved_findings(self) -> tuple[FindingRecord, ...]:
        """Return the findings already marked resolved."""
        return tuple(
            record
            for record in self.findings
            if record.status is FindingStatus.RESOLVED
        )

    def with_runs(self, *, runs: tuple[RunRecord, ...]) -> ReviewState:
        """Return a copy of this state carrying a different run history."""
        return replace(self, runs=runs)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the state for the hidden comment blob.

        Returns:
            JSON-serializable mapping with ``version``, ``runs``, and
            ``findings`` keys (plus ``truncated`` when pruning occurred).
        """
        payload: dict[str, Any] = {
            "version": self.version,
            "runs": [run.to_dict() for run in self.runs],
            "findings": [record.to_dict() for record in self.findings],
        }
        if self.truncated:
            payload["truncated"] = True
        return payload
