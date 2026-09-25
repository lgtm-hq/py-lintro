"""Keep a rerun's verdict equal to the attempt it reruns (#2803).

A rerun at the same head resumes the saved state. Before state v6 that state
said only "reviewed, nothing found", so a rerun of a job that failed on a
coverage degradation reviewed nothing and passed. Each run record now keeps
its degradations as
:class:`~lintro.ai.review.models.degradation_record.DegradationRecord` rows,
and the next round at the same head reads the latest one twice:

* :func:`redo_scope`, at resume planning: a per-file reason whose files were
  credited anyway (a split and re-reviewed chunk, a failed adversarial sweep)
  takes its files out of carried coverage, so they are reviewed again.
* :func:`carried_degradations`, at result assembly: a degradation whose work
  this round did not redo is recorded again, so the rerun reports the same
  warning (a narrative reason) or fails the same way (a per-file reason,
  including a redo the budget stopped). Work the round did redo answers for
  itself.

A new head starts fresh: its files are reviewed by hash as before.
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


def latest_degradations(
    *,
    prior: ReviewState | None,
    head_sha: str,
) -> tuple[DegradationRecord, ...]:
    """Return the latest round's degradations when it reviewed this head.

    Args:
        prior: The resumed state, or ``None``.
        head_sha: The head this round reviews.

    Returns:
        The latest run's records at ``head_sha``; empty when there is no
        prior run, the latest run reviewed another head, or its records
        predate v6.
    """
    if prior is None or not prior.runs or not head_sha:
        return ()
    latest = prior.runs[-1]
    if latest.identity.sha != head_sha:
        return ()
    return tuple(
        record for record in latest.coverage.degradations if record.head_sha == head_sha
    )


def _needs_redo(record: DegradationRecord) -> bool:
    """Return whether a record's reason fails the run and is not carried."""
    return (
        record.reason not in NARRATIVE_DEGRADATION_REASONS
        and record.reason not in _CARRIED_BY_COVERAGE
    )


def redo_scope(*, prior: ReviewState | None, head_sha: str) -> RedoScope:
    """Return the files a rerun at ``head_sha`` must review again.

    Args:
        prior: The resumed state, or ``None``.
        head_sha: The head this round reviews.

    Returns:
        The scope; empty unless the latest round at this head recorded a
        per-file reason.
    """
    records = [
        record
        for record in latest_degradations(prior=prior, head_sha=head_sha)
        if _needs_redo(record)
    ]
    return RedoScope(
        paths=frozenset(
            path for record in records for path in record.degradation.paths
        ),
        whole_head=any(not record.degradation.paths for record in records),
    )


def _redone(
    *,
    record: DegradationRecord,
    reviewed: Collection[str],
    steps_ran: Collection[DegradationStep],
) -> bool:
    """Return whether this round redid the work a record degraded.

    Args:
        record: A degradation from the latest round at this head.
        reviewed: Files this round reviewed.
        steps_ran: Once-per-round steps this round ran.

    Returns:
        True when this round's own outcome for that work stands instead.
    """
    if record.step in _FILE_STEPS:
        paths = record.degradation.paths
        if not paths:
            return bool(reviewed)
        return all(path in reviewed for path in paths)
    return record.step in steps_ran


def carried_degradations(
    *,
    prior: ReviewState | None,
    head_sha: str,
    current: Sequence[CoverageDegradation],
    reviewed: Collection[str],
    steps_ran: Collection[DegradationStep],
) -> tuple[CoverageDegradation, ...]:
    """Return the earlier degradations this round records again.

    Args:
        prior: The resumed state, or ``None`` (``--full`` passes ``None``).
        head_sha: The head this round reviews.
        current: The degradations this round recorded itself.
        reviewed: Files this round reviewed.
        steps_ran: Once-per-round steps this round ran.

    Returns:
        The latest same-head round's degradations whose work this round did
        not redo, excluding the ones the coverage records already re-report,
        the run-setup facts every round recomputes, and exact repeats. A
        file-step row takes :data:`CARRIED_CHUNK_INDEX`: no chunk of this
        round read it, so it must not count against this round's chunks.
    """
    carried: list[CoverageDegradation] = []
    for record in latest_degradations(prior=prior, head_sha=head_sha):
        if record.reason in _CARRIED_BY_COVERAGE or record.step is DegradationStep.RUN:
            continue
        if _redone(record=record, reviewed=reviewed, steps_ran=steps_ran):
            continue
        degradation = record.degradation
        if record.step in _FILE_STEPS:
            degradation = replace(degradation, chunk_index=CARRIED_CHUNK_INDEX)
        if degradation in current or degradation in carried:
            continue
        carried.append(degradation)
    return tuple(carried)
