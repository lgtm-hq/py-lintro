"""Halving a chunk and merging its halves back (#2685).

The pure helpers behind the bounded recovery in
:mod:`lintro.ai.review.chunk_split_retry`: how a chunk is split by file, how
two half partials become one, and how a partial is scoped back to the chunk
it answered for.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.ai.review.context import split_unified_diff_by_file
from lintro.ai.review.coverage import review_eligible_paths
from lintro.ai.review.diff_gate import DiffGateCounts
from lintro.ai.review.finding_parser import reject_context_findings
from lintro.ai.review.merge import ChunkReviewPartial, merge_findings
from lintro.ai.review.models.review_chunk import ReviewChunk

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.ai.review.response_pipeline import ChunkReviewRequest

__all__ = ["merge_half_partials", "scope_partial_to_chunk", "split_chunk"]


def _half_read_diff(
    *,
    files: list[str],
    per_file: dict[str, str],
    per_file_read: dict[str, str] | None,
) -> str | None:
    """The delta text of one half, or ``None`` when the half was read whole.

    Args:
        files: The half's files.
        per_file: The parent's whole-PR text per file.
        per_file_read: The parent's read text per file, or ``None`` on a
            full round.

    Returns:
        The half's ``read_diff``; ``None`` when nothing in it was narrowed.
    """
    if per_file_read is None:
        return None
    read = "".join(per_file_read.get(path, "") for path in files)
    whole = "".join(per_file.get(path, "") for path in files)
    return read if read and read != whole else None


def split_chunk(*, chunk: ReviewChunk) -> tuple[ReviewChunk, ReviewChunk] | None:
    """Bisect a chunk by file count into two chunks that keep its identity.

    Each half carries the per-file diff sections of its own files, in the
    chunk's file order, and the parent's id, relationship and metadata note,
    so prompts and progress events keep naming the chunk the run planned.

    Args:
        chunk: The chunk to split.

    Returns:
        The two halves, or ``None`` when the chunk has fewer than two files
        and cannot be split.
    """
    if len(chunk.files) < 2:
        return None
    per_file = split_unified_diff_by_file(unified_diff=chunk.diff)
    # A delta round's read text (#2627) splits along the same files.
    per_file_read = (
        split_unified_diff_by_file(unified_diff=chunk.read_diff)
        if chunk.read_diff is not None
        else None
    )
    midpoint = len(chunk.files) // 2
    halves = (list(chunk.files[:midpoint]), list(chunk.files[midpoint:]))
    left, right = (
        ReviewChunk(
            id=chunk.id,
            files=files,
            diff="".join(per_file.get(path, "") for path in files),
            relationship=chunk.relationship,
            metadata_note=chunk.metadata_note,
            # A half none of whose files was narrowed is a whole-diff chunk:
            # its read text equals its whole text, and a scope note would lie.
            read_diff=_half_read_diff(
                files=files,
                per_file=per_file,
                per_file_read=per_file_read,
            ),
            read_since=chunk.read_since,
        )
        for files in halves
    )
    return left, right


def _sum_turns(*, partials: list[ChunkReviewPartial]) -> int | None:
    """Sum the halves' transport-reported turns, or ``None`` if any is unknown.

    Args:
        partials: The halves' partials.

    Returns:
        The total turn count, or ``None`` when a half reported none.
    """
    turns = [partial.turns for partial in partials]
    if any(value is None for value in turns):
        return None
    return sum(value for value in turns if value is not None)


def merge_half_partials(
    *,
    partials: Sequence[ChunkReviewPartial],
) -> ChunkReviewPartial:
    """Fold the partials of a split chunk back into one chunk partial.

    Findings are deduplicated by location; re-read flags, token, cost and
    timing usage are combined.

    Args:
        partials: The halves' partials, in file order.

    Returns:
        One partial standing for the whole chunk.
    """
    ordered = list(partials)
    return ChunkReviewPartial(
        findings=merge_findings(
            findings_groups=[partial.findings for partial in ordered],
        ),
        input_tokens=sum(partial.input_tokens for partial in ordered),
        output_tokens=sum(partial.output_tokens for partial in ordered),
        cost_estimate=sum(partial.cost_estimate for partial in ordered),
        provider_seconds=sum(partial.provider_seconds for partial in ordered),
        context_tokens=sum(partial.context_tokens for partial in ordered),
        turns=_sum_turns(partials=ordered),
        files=tuple(path for partial in ordered for path in partial.files),
        flagged_files=tuple(
            flag for partial in ordered for flag in partial.flagged_files
        ),
        converted_flags=tuple(
            flag for partial in ordered for flag in partial.converted_flags
        ),
        coverage_degradations=tuple(
            item for partial in ordered for item in partial.coverage_degradations
        ),
        diff_gate=sum((partial.diff_gate for partial in ordered), DiffGateCounts()),
    )


def scope_partial_to_chunk(
    *,
    partial: ChunkReviewPartial,
    request: ChunkReviewRequest,
) -> ChunkReviewPartial:
    """Keep only findings on the chunk's own files; the rest become flags.

    The run-level path gate allows any file in the resume queue, so a chunk
    answering about another queued chunk's file (which the repository context
    section may have shown it, #2714) would otherwise post a finding the
    chunk never had the diff for. Findings on other review-eligible files
    become re-read flags, everything else is dropped, exactly as the run-level
    gate does but with the chunk's file set as the allowed set (#2719).

    Args:
        partial: The parsed chunk partial.
        request: The request that produced it (chunk files, run context).

    Returns:
        The partial with out-of-chunk findings converted or dropped.
    """
    kept, flags = reject_context_findings(
        findings=partial.findings,
        allowed_paths=set(request.chunk.files),
        eligible_paths=set(
            review_eligible_paths(
                changed_files=request.context.changed_files,
                skipped=request.context.skipped_files,
            ),
        ),
    )
    if len(kept) == len(partial.findings) and not flags:
        return partial
    return replace(
        partial,
        findings=kept,
        converted_flags=(*partial.converted_flags, *flags),
    )
