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

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from lintro.ai.json_response import parse_review_response_payload
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_summary import ReviewSummary
from lintro.ai.review.narrative_parser import MAX_WALKTHROUGH_BULLETS
from lintro.ai.review.sensitivity import filter_findings_by_policy

if TYPE_CHECKING:
    from lintro.ai.review.models.checklist_answer import ChecklistAnswer
    from lintro.ai.review.models.coverage_degradation import CoverageDegradation
    from lintro.ai.review.models.file_assessment import FileAssessment
    from lintro.ai.review.models.flagged_file import FlaggedFile
    from lintro.ai.review.models.review_finding import ReviewFinding
    from lintro.ai.review.models.summary_bullet import SummaryBullet
    from lintro.ai.review.models.verdict_reasoning import VerdictReasoning
    from lintro.ai.review.sensitivity import ReviewSensitivityPolicy

__all__ = [
    "ChunkReviewPartial",
    "finalize_partials",
    "merge_checklist_answers",
    "merge_file_assessments",
    "merge_findings",
    "merge_pr_summaries",
    "merge_review_results",
    "merge_verdict_reasoning",
    "normalize_checklist_answer_value",
    "parse_review_response",
]


@dataclass(frozen=True, slots=True)
class ChunkReviewPartial:
    """Intermediate review result for one chunk.

    Attributes:
        summary: Flat summary text the chunk returned.
        checklist: Checklist answers for the chunk's slice of the diff.
        findings: Findings the chunk reported, in reported order.
        input_tokens: Prompt tokens the chunk's provider calls consumed.
        output_tokens: Completion tokens the chunk's provider calls produced.
        cost_estimate: Estimated USD cost of the chunk's provider calls.
        pr_summary: Structured summary, or ``None`` when the model returned
            only plain summary text.
        verdict_reasoning: Model-written verdict explanation, or ``None``.
        file_assessments: One-sentence overview per file the chunk assessed.
        files: Repository-relative paths the chunk reviewed. Coverage
            crediting and the synthesis digest key off this set.
        flagged_files: Reviewer re-read requests the chunk reported.
        coverage_degradations: Chunk-level limits that may have suppressed
            findings, such as a findings cap or an output-exhaustion retry.
    """

    summary: str
    checklist: tuple[ChecklistAnswer, ...]
    findings: tuple[ReviewFinding, ...]
    input_tokens: int
    output_tokens: int
    cost_estimate: float
    pr_summary: ReviewSummary | None = None
    verdict_reasoning: VerdictReasoning | None = None
    file_assessments: tuple[FileAssessment, ...] = field(default_factory=tuple)
    files: tuple[str, ...] = field(default_factory=tuple)
    flagged_files: tuple[FlaggedFile, ...] = field(default_factory=tuple)
    coverage_degradations: tuple[CoverageDegradation, ...] = field(
        default_factory=tuple,
    )


def parse_review_response(*, content: str) -> dict[str, Any]:
    """Parse and validate AI review JSON response.

    Args:
        content: Raw or fenced JSON model response.

    Returns:
        Parsed review response dictionary.

    Raises:
        ValueError: When JSON is invalid or missing required keys.
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


def merge_checklist_answers(
    *,
    checklist_groups: list[tuple[ChecklistAnswer, ...]],
) -> tuple[ChecklistAnswer, ...]:
    """Merge checklist answers with yes winning over no.

    Args:
        checklist_groups: Checklist answer tuples from each chunk/pass.

    Returns:
        Merged checklist answers keyed by checklist id.
    """
    by_id: dict[int, ChecklistAnswer] = {}
    for group in checklist_groups:
        for answer in group:
            existing = by_id.get(answer.id)
            if existing is None:
                by_id[answer.id] = answer
                continue
            by_id[answer.id] = _pick_preferred_checklist_answer(
                candidate=answer,
                existing=existing,
            )
    return tuple(sorted(by_id.values(), key=lambda item: item.id))


def merge_review_results(
    *,
    partials: list[ChunkReviewPartial],
) -> ReviewResult:
    """Merge partial chunk results into a single review result shell.

    Args:
        partials: Partial results from each chunk.

    Returns:
        Review result without metadata (caller attaches metadata).
    """
    if not partials:
        return ReviewResult(
            metadata=_placeholder_metadata(),
            summary="No review output.",
            checklist=(),
            findings=(),
        )

    summaries = [partial.summary for partial in partials if partial.summary.strip()]
    summary = summaries[0] if len(summaries) == 1 else "\n\n".join(summaries)

    return ReviewResult(
        metadata=_placeholder_metadata(),
        summary=summary,
        checklist=merge_checklist_answers(
            checklist_groups=[partial.checklist for partial in partials],
        ),
        findings=merge_findings(
            findings_groups=[partial.findings for partial in partials],
        ),
        pr_summary=merge_pr_summaries(partials=partials),
        verdict_reasoning=merge_verdict_reasoning(partials=partials),
        file_assessments=merge_file_assessments(partials=partials),
    )


def merge_pr_summaries(
    *,
    partials: list[ChunkReviewPartial],
) -> ReviewSummary | None:
    """Merge structured PR summaries across chunks.

    Each chunk sees only part of the diff, so the headlines are joined and the
    walkthrough bullets concatenated in chunk order, deduplicated by text and
    capped at :data:`MAX_WALKTHROUGH_BULLETS` so a many-chunk review does not
    produce an unreadable wall of bullets.

    Args:
        partials: Partial results from each chunk.

    Returns:
        The merged summary, or ``None`` when no chunk returned one.
    """
    summaries = [
        partial.pr_summary for partial in partials if partial.pr_summary is not None
    ]
    if not summaries:
        return None

    headlines = [summary.headline for summary in summaries if summary.headline]
    bullets: list[SummaryBullet] = []
    seen: set[str] = set()
    for summary in summaries:
        for bullet in summary.walkthrough:
            if bullet.text in seen:
                continue
            seen.add(bullet.text)
            bullets.append(bullet)

    headline = " ".join(headlines)
    if not headline.strip():
        # Every chunk's summary was headline-less (only walkthrough bullets),
        # so there is nothing to join. Returning a ReviewSummary with a blank
        # headline here would let renderers print an empty heading line; None
        # matches what merge_pr_summaries returns when no chunk had a summary
        # at all.
        return None

    return ReviewSummary(
        headline=headline,
        walkthrough=tuple(bullets[:MAX_WALKTHROUGH_BULLETS]),
    )


def merge_verdict_reasoning(
    *,
    partials: list[ChunkReviewPartial],
) -> VerdictReasoning | None:
    """Merge verdict reasoning across chunks.

    The reasoning must stay at most two short paragraphs, so the first chunk
    that produced reasoning wins its prose; only the files-needing-attention
    pointers are unioned across chunks, since a reviewer needs all of them.

    Args:
        partials: Partial results from each chunk.

    Returns:
        The merged reasoning, or ``None`` when no chunk returned any.
    """
    reasonings = [
        partial.verdict_reasoning
        for partial in partials
        if partial.verdict_reasoning is not None
    ]
    if not reasonings:
        return None

    files: list[str] = []
    for reasoning in reasonings:
        files.extend(
            path for path in reasoning.files_needing_attention if path not in files
        )
    return replace(reasonings[0], files_needing_attention=tuple(files))


def merge_file_assessments(
    *,
    partials: list[ChunkReviewPartial],
) -> tuple[FileAssessment, ...]:
    """Merge per-file assessments across chunks.

    Args:
        partials: Partial results from each chunk.

    Returns:
        One assessment per file, first chunk to assess a file winning.
    """
    by_path: dict[str, FileAssessment] = {}
    for partial in partials:
        for assessment in partial.file_assessments:
            by_path.setdefault(assessment.file, assessment)
    return tuple(by_path.values())


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


def normalize_checklist_answer_value(*, answer: str) -> str:
    """Normalize checklist answers to the yes/no contract."""
    normalized = answer.strip().lower()
    if normalized not in {"yes", "no"}:
        return "no"
    return normalized


def _checklist_answer_strength(*, answer: ChecklistAnswer) -> int:
    """Score checklist answers for merge precedence.

    Per epic #991's v3.1 contract, every ``yes`` must map to a finding, so a
    ``yes`` from any chunk strictly wins over a ``no`` regardless of evidence.
    Evidence only breaks ties between two answers of the same polarity. This
    prevents an evidence-backed ``no`` from one chunk silently overturning a
    bare ``yes`` from another and dropping the finding non-deterministically.

    Args:
        answer: Checklist answer to score.

    Returns:
        Strength score: yes-with-evidence 4, yes 3, no-with-evidence 2, no 1.
    """
    has_evidence = bool(answer.evidence.strip())
    if answer.answer == "yes":
        return 4 if has_evidence else 3
    return 2 if has_evidence else 1


def _pick_preferred_checklist_answer(
    *,
    candidate: ChecklistAnswer,
    existing: ChecklistAnswer,
) -> ChecklistAnswer:
    """Pick the stronger checklist answer when merging chunk results."""
    candidate_strength = _checklist_answer_strength(answer=candidate)
    existing_strength = _checklist_answer_strength(answer=existing)
    if candidate_strength >= existing_strength:
        return candidate
    return existing


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
