"""Per-run statistics record persisted in the review state blob.

The record is composed of four value objects — :class:`RunIdentity`,
:class:`RunCoverage`, :class:`RunUsage` and :class:`RunOutcome` — but it is
persisted **flat**: :meth:`RunRecord.to_dict` emits exactly the sticky-state
keys it always has, and :meth:`RunRecord.from_dict` still reads a legacy blob
that never knew about the grouping. The nesting is an in-process shape only,
so no state blob written by an older lintro becomes unreadable and no blob
written here becomes unreadable to one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models._coerce import coerce_float, coerce_int
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_outcome import (
    CONVERGENCE_SCORE_PRECISION,
    RunOutcome,
)
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.transport import resolve_cost_basis

__all__ = ["RunRecord"]


def _strict_bool(value: object) -> bool:
    """Return ``value`` only when it is a real boolean, else ``False``.

    A legacy blob carrying ``"false"`` as a *string* must not truthy-coerce
    into a positive flag.

    Args:
        value: Raw payload value.

    Returns:
        The boolean itself, or ``False`` for anything that is not a bool.
    """
    return value if isinstance(value, bool) else False


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Statistics for one AI review round on a pull request.

    The four groups partition the ~30 facts a round records by *when they are
    known*: the identity is fixed before the review starts, coverage and usage
    are measured while it runs, and the outcome is derived from what it found.

    Attributes:
        identity: Which round this was, on which commit, run by whom.
        coverage: How much of the diff the round actually reviewed.
        usage: Wall-clock time and provider tokens the round consumed.
        outcome: Findings, verdict and recap the round produced.
    """

    identity: RunIdentity = field(default_factory=RunIdentity)
    coverage: RunCoverage = field(default_factory=RunCoverage)
    usage: RunUsage = field(default_factory=RunUsage)
    outcome: RunOutcome = field(default_factory=RunOutcome)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the run record for the hidden state blob.

        The payload is flat and its key order is fixed: the groups are an
        in-process shape, and the wire format predates them.

        Returns:
            JSON-serializable mapping carrying both the v1 aggregate keys and
            the v2 additions. The optional per-round fields are omitted when
            unset, so a record that predates them round-trips byte-identically
            and keeps rendering as "unknown" rather than as zero.
        """
        identity = self.identity
        coverage = self.coverage
        usage = self.usage
        outcome = self.outcome
        payload: dict[str, Any] = {
            "round": identity.round,
            "timestamp": identity.timestamp,
            "sha": identity.sha,
            "model": identity.model,
            "provider": identity.provider,
            "transport": identity.transport,
            "auth_mode": identity.auth_mode,
            "depth": identity.depth,
            "strictness": identity.strictness,
            "files_reviewed": coverage.files_reviewed,
            "files_skipped": coverage.files_skipped,
            "checks": coverage.checks,
            "duration": round(usage.duration, 2),
            "prompt": usage.prompt,
            "completion": usage.completion,
            "total": usage.total,
            "cost": round(usage.cost, 6),
            "estimated": usage.estimated,
            "verdict": str(outcome.verdict),
            "confidence": outcome.confidence,
            "p1": outcome.p1,
            "p2": outcome.p2,
            "p3": outcome.p3,
            "questions": outcome.questions,
            "downgraded": outcome.downgraded,
            "partial": coverage.partial,
            "chunks_reviewed": coverage.chunks_reviewed,
            "chunks_total": coverage.chunks_total,
        }
        if coverage.coverage_limited:
            payload["coverage_limited"] = True
        if usage.cost_basis:
            payload["cost_basis"] = usage.cost_basis
        if outcome.resolved is not None:
            payload["resolved"] = outcome.resolved
        if outcome.open_after is not None:
            payload["open_after"] = outcome.open_after
        if outcome.narrative:
            payload["narrative"] = outcome.narrative
        # A non-finite score is dropped rather than written: json.dumps would
        # emit a bare NaN/Infinity token, which is not valid JSON and would
        # make the whole state blob undecodable for every later round. Omitted
        # reads as "not measured", which is what _optional_score already
        # decodes a corrupt value back to (#2099 review).
        score = outcome.convergence_score
        if score is not None and math.isfinite(score):
            payload["convergence_score"] = round(
                score,
                CONVERGENCE_SCORE_PRECISION,
            )
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunRecord:
        """Rebuild a run record from an untrusted state-blob mapping.

        Missing keys fall back to defaults, so a v1 record parses cleanly and
        simply carries empty v2 fields.

        Args:
            payload: Decoded JSON mapping for one run.

        Returns:
            The parsed run record, with the flat payload split back into its
            four groups.
        """
        return cls(
            identity=_identity_from_payload(payload=payload),
            coverage=_coverage_from_payload(payload=payload),
            usage=_usage_from_payload(payload=payload),
            outcome=_outcome_from_payload(payload=payload),
        )


def _identity_from_payload(*, payload: dict[str, Any]) -> RunIdentity:
    """Parse the identity group out of a flat state-blob mapping.

    Args:
        payload: Decoded JSON mapping for one run.

    Returns:
        The parsed identity group.
    """
    return RunIdentity(
        round=coerce_int(payload.get("round"), default=1) or 1,
        timestamp=str(payload.get("timestamp", "")),
        sha=str(payload.get("sha", "")),
        model=str(payload.get("model", "")),
        provider=str(payload.get("provider", "")),
        transport=str(payload.get("transport", "")),
        auth_mode=str(payload.get("auth_mode", "")),
        depth=coerce_int(payload.get("depth")),
        strictness=str(payload.get("strictness", "")),
    )


def _coverage_from_payload(*, payload: dict[str, Any]) -> RunCoverage:
    """Parse the coverage group out of a flat state-blob mapping.

    Args:
        payload: Decoded JSON mapping for one run.

    Returns:
        The parsed coverage group.
    """
    return RunCoverage(
        files_reviewed=coerce_int(payload.get("files_reviewed")),
        files_skipped=coerce_int(payload.get("files_skipped")),
        checks=coerce_int(payload.get("checks")),
        partial=_strict_bool(payload.get("partial")),
        coverage_limited=_strict_bool(payload.get("coverage_limited")),
        chunks_reviewed=coerce_int(payload.get("chunks_reviewed")),
        chunks_total=coerce_int(payload.get("chunks_total")),
    )


def _usage_from_payload(*, payload: dict[str, Any]) -> RunUsage:
    """Parse the usage group out of a flat state-blob mapping.

    Args:
        payload: Decoded JSON mapping for one run.

    Returns:
        The parsed usage group, with the cost basis derived from the legacy
        ``auth_mode`` + ``estimated`` pair when the key is absent.
    """
    # Strict bool only: a legacy blob carrying "false" as a *string* must
    # not truthy-coerce into an estimated cost basis.
    estimated = _strict_bool(payload.get("estimated"))
    if "cost_basis" in payload:
        cost_basis = _parse_cost_basis(payload.get("cost_basis"))
    else:
        # Legacy records (pre-#1923) derive provenance from auth_mode +
        # estimated so sticky consumers still get a truthful label.
        derived = resolve_cost_basis(
            auth_mode=str(payload.get("auth_mode", "")),
            estimated=estimated,
        )
        cost_basis = derived.value if derived is not None else ""
    return RunUsage(
        duration=coerce_float(payload.get("duration")),
        prompt=coerce_int(payload.get("prompt")),
        completion=coerce_int(payload.get("completion")),
        total=coerce_int(payload.get("total")),
        cost=coerce_float(payload.get("cost")),
        estimated=estimated,
        cost_basis=cost_basis,
    )


def _outcome_from_payload(*, payload: dict[str, Any]) -> RunOutcome:
    """Parse the outcome group out of a flat state-blob mapping.

    Args:
        payload: Decoded JSON mapping for one run.

    Returns:
        The parsed outcome group.
    """
    return RunOutcome(
        verdict=_parse_verdict(payload.get("verdict")),
        confidence=str(payload.get("confidence", "")),
        p1=coerce_int(payload.get("p1")),
        p2=coerce_int(payload.get("p2")),
        p3=coerce_int(payload.get("p3")),
        questions=coerce_int(payload.get("questions")),
        downgraded=coerce_int(payload.get("downgraded")),
        resolved=_optional_count(payload.get("resolved")),
        open_after=_optional_count(payload.get("open_after")),
        narrative=str(payload.get("narrative", "")),
        convergence_score=_optional_score(payload.get("convergence_score")),
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


def _optional_score(value: Any) -> float | None:
    """Parse a convergence score that may be absent from a legacy record.

    Args:
        value: Raw value decoded from the state blob, or ``None`` when the key
            was never written.

    Returns:
        The parsed score, or ``None`` when the key is absent or unusable. A
        corrupted value degrades to "not measured" rather than to ``0.0``: a
        zero is the strongest possible evidence of convergence, and inventing
        one from bad data would stop re-reviewing a PR that still has open
        blockers. Negative values are impossible by construction, so they are
        rejected the same way.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(parsed) or parsed < 0:
            return None
        return round(parsed, CONVERGENCE_SCORE_PRECISION)
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
