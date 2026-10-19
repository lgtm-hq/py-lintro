"""One coverage degradation as the review state remembers it (#2803)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.degradation_step import DegradationStep, step_for_reason
from lintro.ai.review.models._coerce import coerce_int
from lintro.ai.review.models.coverage_degradation import CoverageDegradation

__all__ = ["DegradationRecord"]


@dataclass(frozen=True, slots=True)
class DegradationRecord:
    """A degradation a round recorded, kept in its run record.

    Before v6 the saved state held only booleans and counts per round, so a
    rerun resumed "reviewed, nothing found" and dropped the
    reason the first attempt failed on (#2803). The record keeps the reason,
    the step it came from, the files it hit and the head it was recorded at,
    so the rerun can redo the step or carry the reason forward.

    Attributes:
        degradation: The degradation as the round recorded it, ``paths``
            included.
        step: The step the degradation came from.
        head_sha: The head the round reviewed.
    """

    degradation: CoverageDegradation
    step: DegradationStep
    head_sha: str

    @classmethod
    def from_degradation(
        cls,
        *,
        degradation: CoverageDegradation,
        head_sha: str,
    ) -> DegradationRecord:
        """Record a round's degradation against the head it reviewed.

        Args:
            degradation: The degradation from the round's metadata.
            head_sha: The head the round reviewed.

        Returns:
            The record, with the step derived from the reason.
        """
        return cls(
            degradation=degradation,
            step=step_for_reason(reason=degradation.reason),
            head_sha=head_sha,
        )

    @property
    def reason(self) -> CoverageDegradationReason:
        """Return the recorded reason."""
        return self.degradation.reason

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record for the state blob.

        Returns:
            The degradation's own mapping plus ``split``, ``step``,
            ``head_sha`` and, when set, ``paths``.
        """
        payload = self.degradation.to_dict()
        # Always written here, unlike the envelope, so every record
        # round-trips exactly.
        payload["split"] = self.degradation.split
        payload["step"] = str(self.step)
        payload["head_sha"] = self.head_sha
        if self.degradation.paths:
            payload["paths"] = list(self.degradation.paths)
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> DegradationRecord | None:
        """Parse a record from untrusted JSON.

        Args:
            payload: One decoded entry.

        Returns:
            The record, or ``None`` when the entry is not a mapping or names
            a reason or step this lintro does not know; the caller drops it.
        """
        if not isinstance(payload, dict):
            return None
        try:
            reason = CoverageDegradationReason(str(payload.get("reason", "")))
            step = DegradationStep(str(payload.get("step", "")))
        except ValueError:
            return None
        raw_paths = payload.get("paths")
        paths = (
            tuple(str(path) for path in raw_paths if isinstance(path, str) and path)
            if isinstance(raw_paths, list)
            else ()
        )
        limit = payload.get("limit")
        return cls(
            degradation=CoverageDegradation(
                reason=reason,
                chunk_index=coerce_int(payload.get("chunk_index")),
                # Strict: only a real ``false`` clears it; a missing key keeps
                # the dataclass default and a string never coerces.
                split=payload.get("split", True) is not False
                and not isinstance(payload.get("split", True), str),
                limit=(
                    limit
                    if isinstance(limit, int) and not isinstance(limit, bool)
                    else None
                ),
                detail=str(payload.get("detail", "") or ""),
                paths=paths,
            ),
            step=step,
            head_sha=str(payload.get("head_sha", "") or ""),
        )
