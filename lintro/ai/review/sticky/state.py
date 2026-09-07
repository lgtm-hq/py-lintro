"""State adapters between a review round and the persisted sticky state.

Run-record construction, the per-round narrative, legacy run-mapping upgrades
and the two public state parsers. Rendering lives elsewhere; this module is
about what a round *records*.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from loguru import logger

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.github_badges import severity_counts
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.review_state_codec import decode_state, renumber_if_legacy_v1
from lintro.ai.review.severity_gate import count_downgrades
from lintro.ai.review.sticky.constants import _NARRATIVE_LIMIT, _SENTENCE_BOUNDARY_RE
from lintro.ai.transport import resolve_cost_basis


def matcher_reviewed_paths(*, result: ReviewResult) -> frozenset[str] | None:
    """Return the reviewed-path set the matcher should use.

    An empty ``metadata.reviewed_paths`` on a resume-aware result
    (``coverage`` is set) is a true empty set — including a zero-call
    carried round — so unread findings stay open. Fixture results and
    reviews that predate the coverage field still treat the empty tuple
    as unspecified (``None``) so disappeared findings can resolve.

    Args:
        result: Current review result.

    Returns:
        Paths the provider read, or ``None`` when the field is unspecified.
    """
    if result.metadata.reviewed_paths:
        return frozenset(result.metadata.reviewed_paths)
    if result.coverage is not None:
        return frozenset()
    return None


def stamp_comment_ids(
    *,
    records: tuple[FindingRecord, ...],
    comment_ids: Mapping[str, int] | None,
) -> tuple[FindingRecord, ...]:
    """Attach captured inline comment ids to the records about to be persisted.

    Args:
        records: Records produced by this round's matching.
        comment_ids: Finding key to inline comment id, or ``None`` when no ids
            were captured.

    Returns:
        The records, each carrying its comment id when one is known. A record
        keeps the id it already had when the capture found none, so a failed
        listing never erases the anchor a later round edits.
    """
    if not comment_ids:
        return records
    return tuple(
        (
            replace(record, inline_comment_id=comment_ids[record.key])
            if record.key in comment_ids
            else record
        )
        for record in records
    )


def _state_from_runs(prior_runs: list[dict[str, Any]] | None) -> ReviewState:
    """Build a state object from legacy ``prior_runs`` mappings.

    Args:
        prior_runs: Run mappings recovered from a previous sticky comment, or
            ``None``.

    Returns:
        A state carrying those runs and no finding history.
    """
    runs = tuple(RunRecord.from_dict(run) for run in prior_runs or [])
    return ReviewState(runs=renumber_if_legacy_v1(runs=runs))


def _run_record(
    *,
    request: StickyRequest,
    round_number: int,
    verdict: ReviewVerdict,
    resolved: int,
    open_after: int,
    convergence_score: float,
) -> RunRecord:
    """Build a machine-readable run record from a review result.

    Args:
        request: Inputs for this round. The result, head sha, transport, auth
            mode and cost basis are read from it.
        round_number: 1-based round number for this run.
        verdict: Readiness verdict derived from the open findings.
        resolved: Number of findings this round resolved.
        open_after: Number of findings still open after this round.
        convergence_score: Aggregate score over the findings still open after
            this round (#2099).

    Returns:
        The run record persisted in the state blob.
    """
    result = request.result
    head_sha = request.head_sha
    transport = request.transport
    auth_mode = request.auth_mode
    cost_basis = request.cost_basis
    metadata = result.metadata
    counts = severity_counts(findings=result.findings)
    usage = metadata.token_usage
    effective_auth = auth_mode or metadata.auth_mode
    effective_basis = cost_basis or metadata.cost_basis
    if not effective_basis:
        # Stamp provenance at creation so a fresh render and a re-render of
        # parsed state serialize identically (parse derives the same value
        # for legacy blobs; without this, an error-path re-render would
        # rewrite the blob a "failed round persists state untouched"
        # consumer expects byte-for-byte).
        derived = resolve_cost_basis(
            auth_mode=effective_auth,
            estimated=bool(metadata.token_usage_estimated),
        )
        if derived is None:
            logger.debug(
                "cost_basis derivation returned no value for "
                f"auth_mode={effective_auth!r}; run record keeps an empty "
                "basis (unrecognized auth mode).",
            )
        effective_basis = derived.value if derived is not None else ""
    return RunRecord(
        round=round_number,
        timestamp=metadata.timestamp,
        sha=head_sha,
        model=metadata.model,
        provider=metadata.provider,
        transport=transport or metadata.transport,
        auth_mode=effective_auth,
        cost_basis=effective_basis,
        depth=metadata.depth,
        strictness=metadata.strictness,
        files_reviewed=metadata.files_reviewed,
        files_skipped=max(metadata.files_total - metadata.files_reviewed, 0),
        checks=metadata.checklist_items,
        duration=metadata.duration_seconds,
        prompt=int(usage.get("prompt", 0)),
        completion=int(usage.get("completion", 0)),
        total=int(usage.get("total", 0)),
        cost=metadata.cost_estimate_usd,
        estimated=bool(metadata.token_usage_estimated),
        verdict=verdict,
        p1=counts[Severity.P1],
        p2=counts[Severity.P2],
        p3=counts[Severity.P3],
        questions=sum(1 for finding in result.findings if finding.is_question),
        downgraded=count_downgrades(findings=result.findings),
        partial=bool(metadata.partial),
        coverage_limited=not metadata.findings_coverage_complete,
        chunks_reviewed=metadata.chunks_reviewed,
        chunks_total=metadata.chunks_total,
        resolved=resolved,
        open_after=open_after,
        narrative=_round_narrative(result=result),
        convergence_score=convergence_score,
    )


def _round_narrative(*, result: ReviewResult) -> str:
    """Extract the one-line narrative persisted for this round.

    Args:
        result: Current review result.

    Returns:
        The structured summary's headline when the model produced one, else the
        first sentence of the flat summary, else an empty string. Only the
        first sentence is kept: the recap is one line under a round heading,
        and a paragraph there turns the history into the wall of text the
        sticky redesign exists to undo.
    """
    summary = result.pr_summary
    headline = (summary.headline if summary else "").strip()
    text = headline or result.summary.strip()
    if not text:
        return ""
    # Whitespace is normalized first so a sentence broken across lines is still
    # recognized as one boundary, and so the stored line cannot carry a newline
    # into the recap.
    normalized = " ".join(text.split())
    sentence = _SENTENCE_BOUNDARY_RE.split(normalized, maxsplit=1)[0]
    return sentence[:_NARRATIVE_LIMIT].strip()


def parse_review_state(*, body: str) -> list[dict[str, Any]]:
    """Extract prior run records from a sticky comment's state block.

    Compatibility wrapper over :func:`parse_review_state_v2` for callers that
    only need the run history as plain mappings.

    Args:
        body: Existing sticky comment body.

    Returns:
        List of run records, or an empty list when no valid state is present.
    """
    return [run.to_dict() for run in parse_review_state_v2(body=body).runs]


def parse_review_state_v2(*, body: str) -> ReviewState:
    """Decode the full v2 review state from a sticky comment's state block.

    v1 blobs are migrated in place; a missing, malformed, or unknown-version
    blob yields an empty state rather than raising.

    Args:
        body: Existing sticky comment body.

    Returns:
        The decoded review state.
    """
    return decode_state(body=body)
