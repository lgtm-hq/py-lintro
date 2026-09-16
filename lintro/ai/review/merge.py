"""Cross-chunk merge of partial review results.

A review run splits the diff into chunks and reviews each independently, so
every chunk returns its own partial: findings, checklist answers, narrative
summary, verdict reasoning and per-file assessments for the slice of the diff
it saw. This module folds those partials back into the single
:class:`~lintro.ai.review.models.review_result.ReviewResult` the renderers and
the GitHub writer consume (issue #2301).

The merge rules are behaviour, not implementation detail, and are byte-locked
by the goldens in ``tests/unit/ai/review/golden``: findings deduplicate by
``(file, line, title)`` in first-seen order, a ``yes`` checklist answer from any
chunk beats a ``no`` from any other regardless of evidence, summaries join in
chunk order, and the first chunk to speak wins for verdict prose and per-file
assessments.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lintro.ai.json_response import parse_review_response_payload
from lintro.ai.review.diff_gate import DiffGateCounts
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.sensitivity import filter_findings_by_policy

if TYPE_CHECKING:
    from lintro.ai.review.models.coverage_degradation import CoverageDegradation
    from lintro.ai.review.models.flagged_file import FlaggedFile
    from lintro.ai.review.models.review_finding import ReviewFinding
    from lintro.ai.review.sensitivity import ReviewSensitivityPolicy

__all__ = [
    "ChunkReviewPartial",
    "finalize_partials",
    "merge_findings",
    "merge_review_results",
    "parse_review_response",
]


@dataclass(frozen=True, slots=True)
class ChunkReviewPartial:
    """Intermediate review result for one chunk.

    A chunk answers with findings only (lintro-ops milestone 0, decision A):
    the round's summary, walkthrough and verdict reasoning are written once
    by the synthesis pass over the merged findings, never per chunk.

    Attributes:
        findings: Findings the chunk reported, in reported order.
        input_tokens: Prompt tokens the chunk's provider calls consumed.
        output_tokens: Completion tokens the chunk's provider calls produced.
        cost_estimate: Estimated USD cost of the chunk's provider calls.
        files: Repository-relative paths the chunk reviewed. Coverage
            crediting and the synthesis digest key off this set.
        flagged_files: Reviewer re-read requests the chunk reported.
        coverage_degradations: Chunk-level limits that may have suppressed
            findings, such as an output-exhaustion split or a failed depth
            pass.
        provider_seconds: Wall-clock seconds of the chunk's main provider
            call, for the per-chunk timings (lintro-ops #37).
        turns: Agent turns the transport reported for that call, or ``None``.
        diff_gate: What the diff-bounded gate did to this chunk's findings
            (#2711): outside drops, re-anchors, unanchored keeps.
        truncated: True when the chunk's diff was cut to the context ceiling,
            so the model saw only a prefix of its file. The file stays in
            ``files`` for the synthesis digest and is credited as covered at
            its current hash, so the round converges; the truncation is
            stamped on its coverage record (see :func:`truncated_paths`) and
            ``findings_coverage_complete`` stays false until the file's diff
            changes.
    """

    findings: tuple[ReviewFinding, ...]
    input_tokens: int
    output_tokens: int
    cost_estimate: float
    files: tuple[str, ...] = field(default_factory=tuple)
    flagged_files: tuple[FlaggedFile, ...] = field(default_factory=tuple)
    coverage_degradations: tuple[CoverageDegradation, ...] = field(
        default_factory=tuple,
    )
    provider_seconds: float = 0.0
    turns: int | None = None
    truncated: bool = False
    diff_gate: DiffGateCounts = field(default_factory=DiffGateCounts)


def truncated_paths(*, partials: Iterable[ChunkReviewPartial]) -> set[str]:
    """Return the changed paths whose chunk reviewed only a diff prefix.

    A truncated chunk's file is still credited as covered at its current
    hash, so an unchanged file is not re-read every round; the truncation
    travels with the coverage record instead and is re-reported until the
    file's diff changes. Both the final assembly and the in-flight
    checkpoints stamp the record through here.

    Args:
        partials: Chunk partials finished so far.

    Returns:
        The set of paths reviewed only up to the context-window ceiling.
    """
    return {path for partial in partials if partial.truncated for path in partial.files}


def parse_review_response(*, content: str) -> dict[str, Any]:
    """Parse and validate AI review JSON response.

    A thin re-export of
    :func:`~lintro.ai.json_response.parse_review_response_payload` that keeps
    the parser reachable from the review package. ``ValueError`` from invalid
    JSON or a missing required key propagates to the caller, which turns it
    into a schema-reminder retry rather than treating the chunk as parsed.

    Args:
        content: Raw or fenced JSON model response.

    Returns:
        Parsed review response dictionary.
    """
    return parse_review_response_payload(content=content)


def merge_findings(
    *,
    findings_groups: list[tuple[ReviewFinding, ...]],
) -> tuple[ReviewFinding, ...]:
    """Merge findings from multiple chunks, deduplicating by location.

    Args:
        findings_groups: Finding tuples from each chunk/pass.

    Returns:
        Deduplicated findings preserving first-seen order.
    """
    merged: list[ReviewFinding] = []
    seen: set[tuple[str, int, str]] = set()
    for group in findings_groups:
        for finding in group:
            key = (finding.file, finding.line, finding.title)
            if key in seen:
                continue
            seen.add(key)
            merged.append(finding)
    return tuple(merged)


def merge_review_results(
    *,
    partials: list[ChunkReviewPartial],
) -> ReviewResult:
    """Merge partial chunk results into a single review result shell.

    Chunks carry findings only, so the merge is a deduplicated union of
    their findings. The summary and verdict reasoning are attached later from
    the synthesis pass by the result assembly.

    Args:
        partials: Partial results from each chunk.

    Returns:
        Review result without metadata (caller attaches metadata).
    """
    if not partials:
        return ReviewResult(
            metadata=_placeholder_metadata(),
            summary="No review output.",
            findings=(),
        )
    return ReviewResult(
        metadata=_placeholder_metadata(),
        summary="",
        findings=merge_findings(
            findings_groups=[partial.findings for partial in partials],
        ),
    )


def finalize_partials(
    *,
    partials: list[ChunkReviewPartial],
    policy: ReviewSensitivityPolicy,
) -> tuple[ReviewResult, tuple[ReviewFinding, ...], int]:
    """Merge partials and apply the sensitivity policy.

    Args:
        partials: Completed chunk partials to merge.
        policy: Sensitivity policy used to filter findings.

    Returns:
        Tuple of ``(merged_result, filtered_findings, finding_count)``.
    """
    merged = merge_review_results(partials=partials)
    filtered = filter_findings_by_policy(findings=merged.findings, policy=policy)
    return merged, filtered, len(filtered)


def _placeholder_metadata() -> ReviewMetadata:
    """Return placeholder metadata for merge-only results."""
    return ReviewMetadata(
        model="",
        provider="",
        context_window=0,
        depth=0,
        chunks_total=0,
        chunks_current=0,
        files_reviewed=0,
        files_total=0,
        checklist_items=0,
    )
