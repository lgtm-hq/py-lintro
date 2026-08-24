"""Plan which files a resume round must send to the provider (#2154)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.coverage import (
    BROADCAST_FILENAMES,
    ClassifiedFile,
    classify_files,
    coverage_counts,
    hashes_for_diffs,
    queue_paths,
    review_eligible_paths,
)
from lintro.ai.review.import_graph import importers_of
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.skipped_file import SkippedFile

__all__ = ["ResumePlan", "filter_chunks", "plan_resume", "records_for_reviewed"]


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """Queue and coverage bookkeeping for one resume round.

    Attributes:
        classified: Per-file classification.
        queue: Paths that need a provider read, in cap-safe order.
        hashes: Current normalized patch hash per path.
        eligible: Review-eligible paths.
    """

    classified: tuple[ClassifiedFile, ...]
    queue: tuple[str, ...]
    hashes: dict[str, str]
    eligible: tuple[str, ...]

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
        eligible_paths=eligible,
        current_hashes=hashes,
        coverage=coverage,
        groups=groups,
        import_importers=imports,
        flags=flags,
        pending_invalidations=pending,
        consumed_flags=consumed,
        force_full=force_full,
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
) -> tuple[CoverageRecord, ...]:
    """Merge new coverage entries onto the prior map.

    Args:
        plan: This round's plan.
        reviewed_paths: Paths the provider actually read.
        head_sha: Current head SHA (metadata).
        round_number: Current round.
        prior: Previous state.
        stopped_reason: Mid-round stop, if any.

    Returns:
        Unioned coverage records.
    """
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
        )
        merged[record.identity] = record
    return tuple(merged.values())
