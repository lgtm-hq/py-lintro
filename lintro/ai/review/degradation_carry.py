"""Keep a rerun's verdict equal to the attempt it reruns (#2803).

A rerun resumes the saved state. Before state v6 that state said only
"reviewed, nothing found", so a rerun of a job that failed on a coverage
degradation reviewed nothing and passed. Each run record now keeps its
degradations as
:class:`~lintro.ai.review.models.degradation_record.DegradationRecord` rows,
and the next round reads the latest round's rows twice. What a row owes is
keyed by each file's patch hash, as coverage is, not by the head: a later
head that leaves a degraded file unchanged still owes its redo, and a file
whose content changed is reviewed on its own merits.

* :func:`redo_scope`, at resume planning: a per-file reason whose files were
  credited anyway (a split and re-reviewed chunk, a failed adversarial sweep)
  takes its files out of carried coverage, so they are reviewed again.
* :func:`carried_degradations`, at result assembly: a degradation whose work
  this round did not redo is recorded again, so the rerun reports the same
  warning (a narrative reason) or fails the same way (a per-file reason,
  including a redo the budget stopped). Work the round did redo answers for
  itself.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace

from lintro.ai.review.enums.coverage_degradation_reason import (
    NARRATIVE_DEGRADATION_REASONS,
    CoverageDegradationReason,
)
from lintro.ai.review.enums.degradation_step import DegradationStep
from lintro.ai.review.models.coverage_degradation import (
    CARRIED_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.degradation_record import DegradationRecord
from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "RedoScope",
    "carried_degradations",
    "latest_degradations",
    "redo_scope",
]

#: Reasons the coverage records already carry: a cut file's record keeps
#: ``truncated`` and every round that skips it re-reports the cut (see
#: :attr:`CoverageRecord.truncated`). Redoing it would cut it again at the
#: same ceiling, and carrying the row too would count it twice.
_CARRIED_BY_COVERAGE: frozenset[CoverageDegradationReason] = frozenset(
    {CoverageDegradationReason.DIFF_TRUNCATED},
)

#: Steps that act on files; the other steps run once per round.
_FILE_STEPS: frozenset[DegradationStep] = frozenset(
    {DegradationStep.CHUNK, DegradationStep.ADVERSARIAL_SWEEP},
)


@dataclass(frozen=True, slots=True)
class RedoScope:
    """Files a rerun must review again because the last attempt degraded.

    Attributes:
        paths: Files whose carried coverage is set aside this round.
        whole_head: True when a per-file reason names no files, so every
            eligible file is reviewed again (fail toward more review).
    """

    paths: frozenset[str] = frozenset()
    whole_head: bool = False

    def filter_coverage(
        self,
        *,
        coverage: Sequence[CoverageRecord],
        hashes: dict[str, str],
    ) -> tuple[CoverageRecord, ...]:
        """Drop the coverage the redo sets aside.

        Args:
            coverage: Carried coverage records.
            hashes: Current patch hash per path.

        Returns:
            The records that still credit a file. A record at a redo file's
            hash goes too, so a same-hash sibling cannot credit the file back.
        """
        if self.whole_head:
            return ()
        if not self.paths:
            return tuple(coverage)
        redo_hashes = {hashes[path] for path in self.paths if path in hashes}
        return tuple(
            record
            for record in coverage
            if record.path not in self.paths and record.patch_hash not in redo_hashes
        )


def latest_degradations(*, prior: ReviewState | None) -> tuple[DegradationRecord, ...]:
    """Return the degradations the latest round recorded.

    Args:
        prior: The resumed state, or ``None``.

    Returns:
        The latest run's records; empty when there is no prior run or its
        record predates v6. The head the round reviewed does not matter:
        what a record owes is keyed by each file's patch hash, as coverage
        is, so a later head that leaves a degraded file unchanged still owes
        its redo.
    """
    if prior is None or not prior.runs:
        return ()
    return prior.runs[-1].coverage.degradations


def _degraded_hashes(*, prior: ReviewState | None) -> dict[str, str]:
    """Return the patch hash each file had when the latest round credited it.

    Args:
        prior: The resumed state, or ``None``.

    Returns:
        Path to hash for the coverage records the latest round wrote.
    """
    if prior is None or not prior.runs:
        return {}
    latest_round = prior.runs[-1].identity.round
    return {
        record.path: record.patch_hash
        for record in prior.coverage
        if record.round == latest_round
    }


def _owed_paths(
    *,
    paths: Sequence[str],
    degraded: dict[str, str],
    hashes: dict[str, str],
) -> tuple[str, ...]:
    """Return the files whose degraded work still applies to their content.

    Args:
        paths: The files a degradation named.
        degraded: Hash per file as the degrading round credited it.
        hashes: Current patch hash per file in this round's diff.

    Returns:
        The files still in the diff at the content the degradation hit. A
        file that round credited is owed only at that hash; a changed file
        is reviewed on its own merits. A file it did not credit (a turn
        limit, a lost half) is owed while it is in the diff.
    """
    return tuple(
        path
        for path in paths
        if path in hashes and degraded.get(path, hashes[path]) == hashes[path]
    )


def _needs_redo(record: DegradationRecord) -> bool:
    """Return whether a record's reason fails the run and is not carried."""
    return (
        record.reason not in NARRATIVE_DEGRADATION_REASONS
        and record.reason not in _CARRIED_BY_COVERAGE
    )


def redo_scope(*, prior: ReviewState | None, hashes: dict[str, str]) -> RedoScope:
    """Return the files this round must review again.

    Args:
        prior: The resumed state, or ``None``.
        hashes: Current patch hash per file in this round's diff.

    Returns:
        The scope; empty unless the latest round recorded a per-file reason
        for a file still at the content it degraded.
    """
    records = [
        record for record in latest_degradations(prior=prior) if _needs_redo(record)
    ]
    degraded = _degraded_hashes(prior=prior)
    return RedoScope(
        paths=frozenset(
            path
            for record in records
            for path in _owed_paths(
                paths=record.degradation.paths,
                degraded=degraded,
                hashes=hashes,
            )
        ),
        whole_head=any(not record.degradation.paths for record in records),
    )


def carried_degradations(
    *,
    prior: ReviewState | None,
    current: Sequence[CoverageDegradation],
    reviewed: Collection[str],
    steps_ran: Collection[DegradationStep],
    hashes: dict[str, str],
    head_complete: bool = False,
) -> tuple[CoverageDegradation, ...]:
    """Return the earlier degradations this round records again.

    A file-step row is judged per file. A file no longer owes the row when
    this round reviewed it, attempted it and recorded its own degradation
    for it (a redo that failed again reports that failure), or changed its
    content; the row is carried for the files that still owe it. The redo
    runs at this round's depth, so a depth-3 sweep failure is redone by a
    depth-2 round that reviews the file: the sweep is not part of that
    round's work. A row naming no files sent the whole head back, so only a
    round that leaves the head complete redoes it. A once-per-round step is
    redone when this round ran it.

    Args:
        prior: The resumed state, or ``None`` (``--full`` passes ``None``).
        current: The degradations this round recorded itself.
        reviewed: Files this round reviewed.
        steps_ran: Once-per-round steps this round ran.
        hashes: Current patch hash per file in this round's diff.
        head_complete: Whether every eligible file is covered at the head
            after this round. False by default, so a caller that does not know
            carries a whole-head row rather than dropping it.

    Returns:
        The latest round's degradations this round did not redo, excluding
        the ones the coverage records already re-report, the run-setup facts
        every round recomputes, and exact repeats. A file-step row takes
        :data:`CARRIED_CHUNK_INDEX`: no chunk of this round read it, so it
        must not count against this round's chunks.
    """
    attempted = {
        path
        for item in current
        if item.chunk_index != CARRIED_CHUNK_INDEX
        for path in item.paths
    }
    degraded = _degraded_hashes(prior=prior)
    carried: list[CoverageDegradation] = []
    for record in latest_degradations(prior=prior):
        if record.reason in _CARRIED_BY_COVERAGE or record.step is DegradationStep.RUN:
            continue
        degradation = record.degradation
        if record.step in _FILE_STEPS:
            owed = tuple(
                path
                for path in _owed_paths(
                    paths=degradation.paths,
                    degraded=degraded,
                    hashes=hashes,
                )
                if path not in reviewed and path not in attempted
            )
            if degradation.paths and not owed:
                continue
            if not degradation.paths and reviewed and head_complete:
                continue
            degradation = replace(
                degradation,
                chunk_index=CARRIED_CHUNK_INDEX,
                paths=owed,
            )
        elif record.step in steps_ran:
            continue
        if degradation in current or degradation in carried:
            continue
        carried.append(degradation)
    return tuple(carried)
