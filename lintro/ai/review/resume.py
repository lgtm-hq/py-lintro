"""Plan which files a resume round must send to the provider (#2154)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.coverage import (
    BROADCAST_FILENAMES,
    ClassifiedFile,
    ClassifyFilesRequest,
    classify_files,
    coverage_counts,
    hashes_for_diffs,
    newest_records_by_hash,
    own_records_at_hash,
    queue_paths,
    review_eligible_paths,
)
from lintro.ai.review.delta import open_thread_paths
from lintro.ai.review.enums.file_review_need import FileReviewNeed
from lintro.ai.review.import_graph import importers_of
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.skipped_file import SkippedFile

__all__ = [
    "ResumePlan",
    "carried_truncated_paths",
    "filter_chunks",
    "plan_resume",
    "records_for_reviewed",
]


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """Queue and coverage bookkeeping for one resume round.

    Attributes:
        classified: Per-file classification.
        queue: Paths that need a provider read, in cap-safe order.
        hashes: Current normalized patch hash per path.
        eligible: Review-eligible paths.
        reviewed_ranges: ``(path, start, end)`` new-file line ranges a delta
            round (#2627) reads for the files it narrowed; set by the run
            planner after the delta is applied so the mid-run checkpoints
            and the final round match on the same inputs. Empty on a full
            round.
    """

    classified: tuple[ClassifiedFile, ...]
    queue: tuple[str, ...]
    hashes: dict[str, str]
    eligible: tuple[str, ...]
    reviewed_ranges: tuple[tuple[str, int, int], ...] = ()

    def counts(self, *, reviewed_now: Sequence[str]) -> CoverageCounts:
        """Return counters after the provider finished *reviewed_now*."""
        return coverage_counts(
            classified=self.classified,
            reviewed_now=reviewed_now,
        )


def plan_resume(
    *,
    context: ReviewContext,
    prior: ReviewState | None,
    extra_skips: Sequence[SkippedFile] = (),
    groups: Sequence[Sequence[str]] = (),
    force_full: bool = False,
) -> ResumePlan:
    """Classify the current diff against prior coverage.

    Args:
        context: Collected review context.
        prior: Artifact or local ledger state; empty on first run.
        extra_skips: Chunker / agent-scope skips.
        groups: Semantic groups from this round's chunker.
        force_full: Discard carried coverage (``--full``).

    Returns:
        Queue and hashes for this round.
    """
    diffs = split_unified_diff_by_file(unified_diff=context.unified_diff)
    hashes = hashes_for_diffs(diffs=diffs)
    eligible = review_eligible_paths(
        changed_files=context.changed_files,
        skipped=(*context.skipped_files, *extra_skips),
    )
    coverage = () if prior is None or force_full else prior.coverage
    flags = () if prior is None or force_full else prior.flagged_files
    pending = () if prior is None or force_full else prior.pending_invalidations
    consumed = () if prior is None or force_full else prior.consumed_flags
    import_targets = {
        path for path in eligible if path.rsplit("/", 1)[-1] not in BROADCAST_FILENAMES
    }
    imports = importers_of(
        changed_paths=set(eligible),
        contents=context.post_image_files,
        directly_changed=import_targets,
    )
    classified = classify_files(
        request=ClassifyFilesRequest(
            eligible_paths=eligible,
            current_hashes=hashes,
            coverage=coverage,
            groups=groups,
            import_importers=imports,
            flags=flags,
            pending_invalidations=pending,
            consumed_flags=consumed,
            open_thread_paths=(
                () if prior is None or force_full else open_thread_paths(prior=prior)
            ),
            force_full=force_full,
        ),
    )
    return ResumePlan(
        classified=classified,
        queue=queue_paths(classified=classified),
        hashes=hashes,
        eligible=eligible,
    )


def filter_chunks(
    *,
    chunks: list[ReviewChunk],
    queue: Sequence[str],
) -> list[ReviewChunk]:
    """Keep chunks that still contain a file needing review.

    Covered group-mates stay in a mixed chunk as read-only context; the
    chunk is dropped only when every file is already covered. Remaining
    chunks are ordered by the first queued file they contain so a
    capped serial run cannot invert never-reviewed → changed → flagged
    → invalidated priority.

    Args:
        chunks: Chunks from the grouper (full changed-file set).
        queue: Paths that need review, in cap-safe order.

    Returns:
        Chunks that still need work, in queue order.
    """
    if not queue:
        return []
    rank = {path: index for index, path in enumerate(queue)}
    kept = [chunk for chunk in chunks if any(path in rank for path in chunk.files)]
    kept.sort(
        key=lambda chunk: min(rank[path] for path in chunk.files if path in rank),
    )
    return kept


def records_for_reviewed(
    *,
    plan: ResumePlan,
    reviewed_paths: Sequence[str],
    head_sha: str,
    round_number: int,
    prior: ReviewState | None,
    stopped_reason: str = "",
    truncated_paths: Collection[str] = (),
) -> tuple[CoverageRecord, ...]:
    """Merge new coverage entries onto the prior map.

    Args:
        plan: This round's plan.
        reviewed_paths: Paths the provider actually read.
        head_sha: Current head SHA (metadata).
        round_number: Current round.
        prior: Previous state.
        stopped_reason: Mid-round stop, if any.
        truncated_paths: Reviewed paths whose chunk was cut to the
            context-window ceiling. Their records carry ``truncated``, and
            so does every same-hash sibling credited through them: an
            identical diff that inherited coverage inherited the cut too.

    Returns:
        Unioned coverage records.
    """
    truncated_hashes = {
        plan.hashes[path] for path in truncated_paths if plan.hashes.get(path)
    }
    merged: dict[tuple[str, str], CoverageRecord] = {}
    if prior is not None:
        for record in prior.coverage:
            merged[record.identity] = record
    reviewed = set(reviewed_paths)
    for item in plan.classified:
        if item.path not in reviewed:
            continue
        record = CoverageRecord(
            path=item.path,
            patch_hash=item.patch_hash,
            reviewed_sha=head_sha,
            round=round_number,
            stopped_reason=stopped_reason,
            truncated=(
                item.path in truncated_paths or item.patch_hash in truncated_hashes
            ),
        )
        merged[record.identity] = record
    return tuple(merged.values())


def carried_truncated_paths(
    *,
    plan: ResumePlan,
    prior: ReviewState | None,
) -> tuple[str, ...]:
    """Return covered files whose carried coverage record is truncated.

    A file reviewed only up to the context-window ceiling is credited at its
    hash so the round converges, but the gap is real until the diff changes:
    every round that skips the file as covered re-reports it (lintro-ops
    #37). A new hash re-reviews the file and writes a fresh record, which
    clears the marker or sets it again.

    Args:
        plan: This round's plan.
        prior: Previous state, or ``None`` on a first run.

    Returns:
        Sorted paths classified ``COVERED`` this round that still carry only
        a prefix review. A file's own latest record at its current hash is
        authoritative over any sibling's record of the same or an earlier
        round: a complete re-review at that hash clears the file even while
        a stale sibling record at the same hash stays marked. Only a strictly
        newer record at the same hash from another path overrides it, because
        a later complete review of identical content is a complete review of
        this file too. A file with no record of its own at that hash — a
        sampled sibling that inherited coverage — takes the newest record at
        the hash, however many rounds ago it was written (see
        :func:`~lintro.ai.review.coverage_rounds.truncated_patch_hashes`).
    """
    if prior is None:
        return ()
    own = own_records_at_hash(prior.coverage)
    newest = newest_records_by_hash(prior.coverage)
    carried: list[str] = []
    for item in plan.classified:
        if item.need is not FileReviewNeed.COVERED:
            continue
        record = own.get((item.path, item.patch_hash))
        latest = newest.get(item.patch_hash)
        if record is not None and (latest is None or latest.round <= record.round):
            latest = record
        if latest is not None and latest.truncated:
            carried.append(item.path)
    return tuple(sorted(carried))
