"""Result of matching one review round's findings against prior state."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.models.finding_record import FindingRecord

__all__ = ["FindingMatchResult"]


@dataclass(frozen=True, slots=True)
class FindingMatchResult:
    """Per-round transitions plus the merged finding set to persist.

    Attributes:
        records: Full tracked finding set to persist after the round — every
            current-round finding plus every prior record that survived. Order
            is unspecified; consumers that need a particular ordering must sort
            (for example by ``since_round`` or ``severity``).
        new: Findings seen for the first time in this round.
        carried: Findings that were already open and are still reported.
        resolved: Findings that were open and are absent from this round.
        regressed: Findings that were resolved and are reported again.
        outcomes: Map of ``fingerprint#ordinal`` to the transition assigned in
            this round. Records untouched by this round are absent.
    """

    records: tuple[FindingRecord, ...] = field(default_factory=tuple)
    new: tuple[FindingRecord, ...] = field(default_factory=tuple)
    carried: tuple[FindingRecord, ...] = field(default_factory=tuple)
    resolved: tuple[FindingRecord, ...] = field(default_factory=tuple)
    regressed: tuple[FindingRecord, ...] = field(default_factory=tuple)
    outcomes: dict[str, FindingMatchOutcome] = field(default_factory=dict)

    def outcome_for(self, *, record: FindingRecord) -> FindingMatchOutcome | None:
        """Return the transition assigned to ``record`` in this round.

        Args:
            record: Tracked finding record to look up.

        Returns:
            The outcome, or ``None`` when the round did not touch the record.
        """
        return self.outcomes.get(record.key)
