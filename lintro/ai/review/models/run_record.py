"""Per-run statistics record persisted in the review state blob."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models._coerce import coerce_float, coerce_int
from lintro.ai.transport import resolve_cost_basis

__all__ = ["RunRecord"]


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Statistics for one AI review round on a pull request.

    The v1 aggregate keys (``timestamp``, ``model``, ``prompt``, ``total``,
    ``cost``, severity counts, …) are preserved verbatim in the serialized form
    so existing sticky-comment rendering keeps working across the migration.

    Attributes:
        round: 1-based review round number on this PR.
        timestamp: ISO 8601 UTC timestamp of the run.
        sha: Head commit sha reviewed in this round.
        model: Model identifier used for the review.
        provider: Provider name (anthropic, openai, …).
        transport: Provider transport used (for example ``api`` or ``cli``).
        auth_mode: Authentication mode used by the transport (for example
            ``api_key`` or ``subscription``).
        cost_basis: How ``cost`` should be read (``billed``, ``estimated``,
            or ``unpriceable``) (#1923).
        depth: Review depth level.
        strictness: Sensitivity preset applied.
        files_reviewed: Number of changed files included in the review.
        files_skipped: Number of changed files excluded from the review.
        checks: Number of checklist items in the prompt.
        duration: Wall-clock duration in seconds.
        prompt: Prompt (input) tokens consumed.
        completion: Completion (output) tokens produced.
        total: Total tokens consumed.
        cost: Estimated cost in USD.
        estimated: True when token counts were estimated locally.
        verdict: Readiness verdict derived from open findings after this round.
        confidence: Aggregate confidence label reported for the round.
        p1: Count of P1 findings reported in this round.
        p2: Count of P2 findings reported in this round.
        p3: Count of P3 findings reported in this round.
        questions: Count of entries reported as questions rather than
            findings in this round (#1925). Excluded from ``p1``/``p2``/``p3``
            and from the derived verdict.
        downgraded: Count of P1 findings the evidence gate downgraded to P2 in
            this round (#1925). Recorded per run so severity inflation, and
            how much of it the gate absorbed, stays visible over time rather
            than being an invisible parse-time edit.
        partial: True when the review stopped before every chunk was reviewed.
        chunks_reviewed: Number of chunks actually reviewed.
        chunks_total: Total number of chunks in the diff.
        resolved: Number of findings this round resolved. ``None`` on a record
            persisted before the field existed — history renders that as ``—``
            rather than as a fabricated zero, which would read as "this round
            fixed nothing".
        open_after: Number of findings still open *after* this round, which is
            what a reader of the history actually wants to know. ``None`` on a
            legacy record, where only the raised count was ever stored.
        narrative: One-line recap of the round in the model's own words, taken
            from the structured summary headline (or the review summary's first
            sentence). Empty when the model produced neither.
    """

    round: int = 1
    timestamp: str = ""
    sha: str = ""
    model: str = ""
    provider: str = ""
    transport: str = ""
    auth_mode: str = ""
    cost_basis: str = ""
    depth: int = 0
    strictness: str = ""
    files_reviewed: int = 0
    files_skipped: int = 0
    checks: int = 0
    duration: float = 0.0
    prompt: int = 0
    completion: int = 0
    total: int = 0
    cost: float = 0.0
    estimated: bool = False
    verdict: ReviewVerdict = ReviewVerdict.READY
    confidence: str = ""
    p1: int = 0
    p2: int = 0
    p3: int = 0
    questions: int = 0
    downgraded: int = 0
    partial: bool = False
    chunks_reviewed: int = 0
    chunks_total: int = 0
    resolved: int | None = None
    open_after: int | None = None
    narrative: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the run record for the hidden state blob.

        Returns:
            JSON-serializable mapping carrying both the v1 aggregate keys and
            the v2 additions. The optional per-round fields are omitted when
            unset, so a record that predates them round-trips byte-identically
            and keeps rendering as "unknown" rather than as zero.
        """
        payload: dict[str, Any] = {
            "round": self.round,
            "timestamp": self.timestamp,
            "sha": self.sha,
            "model": self.model,
            "provider": self.provider,
            "transport": self.transport,
            "auth_mode": self.auth_mode,
            "depth": self.depth,
            "strictness": self.strictness,
            "files_reviewed": self.files_reviewed,
            "files_skipped": self.files_skipped,
            "checks": self.checks,
            "duration": round(self.duration, 2),
            "prompt": self.prompt,
            "completion": self.completion,
            "total": self.total,
            "cost": round(self.cost, 6),
            "estimated": self.estimated,
            "verdict": str(self.verdict),
            "confidence": self.confidence,
            "p1": self.p1,
            "p2": self.p2,
            "p3": self.p3,
            "questions": self.questions,
            "downgraded": self.downgraded,
            "partial": self.partial,
            "chunks_reviewed": self.chunks_reviewed,
            "chunks_total": self.chunks_total,
        }
        if self.cost_basis:
            payload["cost_basis"] = self.cost_basis
        if self.resolved is not None:
            payload["resolved"] = self.resolved
        if self.open_after is not None:
            payload["open_after"] = self.open_after
        if self.narrative:
            payload["narrative"] = self.narrative
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunRecord:
        """Rebuild a run record from an untrusted state-blob mapping.

        Missing keys fall back to defaults, so a v1 record parses cleanly and
        simply carries empty v2 fields.

        Args:
            payload: Decoded JSON mapping for one run.

        Returns:
            The parsed run record.
        """
        auth_mode = str(payload.get("auth_mode", ""))
        estimated = bool(payload.get("estimated", False))
        if "cost_basis" in payload:
            cost_basis = _parse_cost_basis(payload.get("cost_basis"))
        else:
            # Legacy records (pre-#1923) derive provenance from auth_mode +
            # estimated so sticky consumers still get a truthful label.
            derived = resolve_cost_basis(auth_mode=auth_mode, estimated=estimated)
            cost_basis = derived.value if derived is not None else ""
        return cls(
            round=coerce_int(payload.get("round"), default=1) or 1,
            timestamp=str(payload.get("timestamp", "")),
            sha=str(payload.get("sha", "")),
            model=str(payload.get("model", "")),
            provider=str(payload.get("provider", "")),
            transport=str(payload.get("transport", "")),
            auth_mode=auth_mode,
            cost_basis=cost_basis,
            depth=coerce_int(payload.get("depth")),
            strictness=str(payload.get("strictness", "")),
            files_reviewed=coerce_int(payload.get("files_reviewed")),
            files_skipped=coerce_int(payload.get("files_skipped")),
            checks=coerce_int(payload.get("checks")),
            duration=coerce_float(payload.get("duration")),
            prompt=coerce_int(payload.get("prompt")),
            completion=coerce_int(payload.get("completion")),
            total=coerce_int(payload.get("total")),
            cost=coerce_float(payload.get("cost")),
            estimated=estimated,
            verdict=_parse_verdict(payload.get("verdict")),
            confidence=str(payload.get("confidence", "")),
            p1=coerce_int(payload.get("p1")),
            p2=coerce_int(payload.get("p2")),
            p3=coerce_int(payload.get("p3")),
            questions=coerce_int(payload.get("questions")),
            downgraded=coerce_int(payload.get("downgraded")),
            partial=bool(payload.get("partial", False)),
            chunks_reviewed=coerce_int(payload.get("chunks_reviewed")),
            chunks_total=coerce_int(payload.get("chunks_total")),
            resolved=_optional_count(payload.get("resolved")),
            open_after=_optional_count(payload.get("open_after")),
            narrative=str(payload.get("narrative", "")),
        )


def _parse_cost_basis(value: Any) -> str:
    """Parse a stored cost-basis label from an untrusted state blob.

    Args:
        value: Raw cost_basis value decoded from the state blob.

    Returns:
        A canonical CostBasis value string, or empty when unrecognized.
    """
    try:
        return CostBasis(str(value).lower()).value
    except ValueError:
        logger.debug("Unrecognized stored cost_basis {!r}; leaving empty", value)
        return ""


def _optional_count(value: Any) -> int | None:
    """Parse a count that may be absent from a legacy record.

    Args:
        value: Raw value decoded from the state blob, or ``None`` when the key
            was never written.

    Returns:
        The parsed count, or ``None`` when the key is absent. A present but
        unparsable value also yields ``None``: rendering "unknown" is honest,
        whereas coercing it to zero would claim the round fixed nothing.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        try:
            return max(int(float(value)), 0)
        except (TypeError, ValueError, OverflowError):
            # ``int(float("inf"))`` raises OverflowError, and a corrupted blob
            # must degrade to "unknown" rather than abort the whole decode.
            return None
    return None


def _parse_verdict(value: Any) -> ReviewVerdict:
    """Parse a stored verdict label from an untrusted state blob.

    An unrecognized label must never fail open: reporting ``READY`` for a
    corrupted or renamed value would fabricate a clean bill of health for a run
    that may have been blocked. The neutral middle value is used instead, and
    the anomaly is logged.

    Args:
        value: Raw verdict value decoded from the state blob.

    Returns:
        The parsed verdict, or ``CHANGES_REQUESTED`` when unrecognized or
        absent. A v1 record carries no verdict key at all, so that absence is
        treated as the same neutral fallback without logging — it is expected
        on every migrated legacy run, not an anomaly.
    """
    if value is None:
        return ReviewVerdict.CHANGES_REQUESTED
    try:
        return ReviewVerdict(str(value).lower())
    except ValueError:
        logger.debug(
            f"Unrecognized stored review verdict {value!r}; treating as "
            f"{ReviewVerdict.CHANGES_REQUESTED}",
        )
        return ReviewVerdict.CHANGES_REQUESTED
