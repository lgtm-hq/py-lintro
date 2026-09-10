"""Build the :class:`RunRecord` one review round leaves behind.

The factory sits next to the model rather than inside the sticky package
because what a round *records* is not a rendering decision: the same record
feeds the mission-control board, the run-history archive and the local state
ledger. Each of the four value objects is assembled by its own function, so a
new fact lands in exactly one of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.github_badges import severity_counts
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_outcome import NARRATIVE_LIMIT, RunOutcome
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.severity_gate import count_downgrades
from lintro.ai.transport import resolve_cost_basis

__all__ = ["RoundTotals", "round_narrative", "run_record_from_result"]

#: End of the first sentence of a round narrative. Terminators other than the
#: period are matched too: a headline ending in "?" or "!" is one sentence, and
#: splitting on ". " alone would persist the whole paragraph after it.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, kw_only=True, slots=True)
class RoundTotals:
    """Per-round numbers the record cannot derive from the result alone.

    Attributes:
        round_number: 1-based round number for this run.
        verdict: Readiness verdict derived from the open findings.
        resolved: Number of findings this round resolved.
        open_after: Number of findings still open after this round.
        convergence_score: Aggregate score over the findings still open after
            this round (#2099).
    """

    round_number: int
    verdict: ReviewVerdict
    resolved: int
    open_after: int
    convergence_score: float


def run_record_from_result(
    *,
    request: StickyRequest,
    totals: RoundTotals,
) -> RunRecord:
    """Build a machine-readable run record from a review result.

    Args:
        request: Inputs for this round. The result, head sha, transport, auth
            mode and cost basis are read from it.
        totals: Round numbers the result does not carry — the round number,
            the derived verdict, the resolved/open counts and the score.

    Returns:
        The run record persisted in the state blob.
    """
    result = request.result
    metadata = result.metadata
    effective_auth = request.auth_mode or metadata.auth_mode
    return RunRecord(
        identity=_identity(
            request=request,
            round_number=totals.round_number,
            auth_mode=effective_auth,
        ),
        coverage=_coverage(result=result),
        usage=_usage(
            request=request,
            auth_mode=effective_auth,
        ),
        outcome=_outcome(result=result, totals=totals),
    )


def _identity(
    *,
    request: StickyRequest,
    round_number: int,
    auth_mode: str,
) -> RunIdentity:
    """Assemble the identity group for this round.

    Args:
        request: Inputs for this round.
        round_number: 1-based round number for this run.
        auth_mode: Authentication mode the transport actually used.

    Returns:
        The identity group.
    """
    metadata = request.result.metadata
    return RunIdentity(
        round=round_number,
        timestamp=metadata.timestamp,
        sha=request.head_sha,
        model=metadata.model,
        provider=metadata.provider,
        transport=request.transport or metadata.transport,
        auth_mode=auth_mode,
        depth=metadata.depth,
        strictness=metadata.strictness,
    )


def _coverage(*, result: ReviewResult) -> RunCoverage:
    """Assemble the coverage group for this round.

    Args:
        result: Current review result.

    Returns:
        The coverage group.
    """
    metadata = result.metadata
    return RunCoverage(
        files_reviewed=metadata.files_reviewed,
        files_skipped=max(metadata.files_total - metadata.files_reviewed, 0),
        checks=metadata.checklist_items,
        partial=bool(metadata.partial),
        coverage_limited=not metadata.findings_coverage_complete,
        chunks_reviewed=metadata.chunks_reviewed,
        chunks_total=metadata.chunks_total,
    )


def _usage(*, request: StickyRequest, auth_mode: str) -> RunUsage:
    """Assemble the usage group for this round.

    Args:
        request: Inputs for this round.
        auth_mode: Authentication mode the transport actually used.

    Returns:
        The usage group, carrying a cost basis stamped at creation time.
    """
    metadata = request.result.metadata
    tokens = metadata.token_usage
    return RunUsage(
        duration=metadata.duration_seconds,
        prompt=int(tokens.get("prompt", 0)),
        completion=int(tokens.get("completion", 0)),
        total=int(tokens.get("total", 0)),
        cost=metadata.cost_estimate_usd,
        estimated=bool(metadata.token_usage_estimated),
        cost_basis=_cost_basis(
            metadata=metadata,
            requested=request.cost_basis,
            auth_mode=auth_mode,
        ),
    )


def _cost_basis(
    *,
    metadata: ReviewMetadata,
    requested: str,
    auth_mode: str,
) -> str:
    """Resolve how this round's cost should be read.

    Provenance is stamped at creation so a fresh render and a re-render of
    parsed state serialize identically (parse derives the same value for
    legacy blobs; without this, an error-path re-render would rewrite the blob
    a "failed round persists state untouched" consumer expects byte-for-byte).

    Args:
        metadata: Metadata of the current review result.
        requested: Cost basis carried by the sticky request, when any.
        auth_mode: Authentication mode the transport actually used.

    Returns:
        A canonical cost-basis label, or empty when it cannot be derived.
    """
    basis = requested or metadata.cost_basis
    if basis:
        return basis
    derived = resolve_cost_basis(
        auth_mode=auth_mode,
        estimated=bool(metadata.token_usage_estimated),
    )
    if derived is None:
        logger.debug(
            "cost_basis derivation returned no value for "
            f"auth_mode={auth_mode!r}; run record keeps an empty "
            "basis (unrecognized auth mode).",
        )
        return ""
    return derived.value


def _outcome(*, result: ReviewResult, totals: RoundTotals) -> RunOutcome:
    """Assemble the outcome group for this round.

    Args:
        result: Current review result.
        totals: Round numbers the result does not carry.

    Returns:
        The outcome group.
    """
    counts = severity_counts(findings=result.findings)
    return RunOutcome(
        verdict=totals.verdict,
        p1=counts[Severity.P1],
        p2=counts[Severity.P2],
        p3=counts[Severity.P3],
        questions=sum(1 for finding in result.findings if finding.is_question),
        downgraded=count_downgrades(findings=result.findings),
        resolved=totals.resolved,
        open_after=totals.open_after,
        narrative=round_narrative(result=result),
        convergence_score=totals.convergence_score,
    )


def round_narrative(*, result: ReviewResult) -> str:
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
    return sentence[:NARRATIVE_LIMIT].strip()
