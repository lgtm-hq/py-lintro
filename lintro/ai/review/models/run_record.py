"""Per-run statistics record persisted in the review state blob."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lintro.ai.review.enums.review_verdict import ReviewVerdict

__all__ = ["RunRecord"]


def _coerce_int(value: Any, *, default: int = 0) -> int:
    """Coerce an untrusted JSON value to an int, falling back to a default."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    """Coerce an untrusted JSON value to a float, falling back to a default."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
            ``api-key`` or ``subscription``).
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
        partial: True when the review stopped before every chunk was reviewed.
        chunks_reviewed: Number of chunks actually reviewed.
        chunks_total: Total number of chunks in the diff.
    """

    round: int = 1
    timestamp: str = ""
    sha: str = ""
    model: str = ""
    provider: str = ""
    transport: str = ""
    auth_mode: str = ""
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
    partial: bool = False
    chunks_reviewed: int = 0
    chunks_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the run record for the hidden state blob.

        Returns:
            JSON-serializable mapping carrying both the v1 aggregate keys and
            the v2 additions.
        """
        return {
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
            "partial": self.partial,
            "chunks_reviewed": self.chunks_reviewed,
            "chunks_total": self.chunks_total,
        }

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
        return cls(
            round=_coerce_int(payload.get("round"), default=1) or 1,
            timestamp=str(payload.get("timestamp", "")),
            sha=str(payload.get("sha", "")),
            model=str(payload.get("model", "")),
            provider=str(payload.get("provider", "")),
            transport=str(payload.get("transport", "")),
            auth_mode=str(payload.get("auth_mode", "")),
            depth=_coerce_int(payload.get("depth")),
            strictness=str(payload.get("strictness", "")),
            files_reviewed=_coerce_int(payload.get("files_reviewed")),
            files_skipped=_coerce_int(payload.get("files_skipped")),
            checks=_coerce_int(payload.get("checks")),
            duration=_coerce_float(payload.get("duration")),
            prompt=_coerce_int(payload.get("prompt")),
            completion=_coerce_int(payload.get("completion")),
            total=_coerce_int(payload.get("total")),
            cost=_coerce_float(payload.get("cost")),
            estimated=bool(payload.get("estimated", False)),
            verdict=_parse_verdict(payload.get("verdict")),
            confidence=str(payload.get("confidence", "")),
            p1=_coerce_int(payload.get("p1")),
            p2=_coerce_int(payload.get("p2")),
            p3=_coerce_int(payload.get("p3")),
            partial=bool(payload.get("partial", False)),
            chunks_reviewed=_coerce_int(payload.get("chunks_reviewed")),
            chunks_total=_coerce_int(payload.get("chunks_total")),
        )


def _parse_verdict(value: Any) -> ReviewVerdict:
    """Parse a verdict label, defaulting to ``READY`` for unknown input."""
    try:
        return ReviewVerdict(str(value).lower())
    except ValueError:
        return ReviewVerdict.READY
