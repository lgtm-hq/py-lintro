"""One recorded coverage degradation for a review chunk (#2003)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)

__all__ = ["CARRIED_CHUNK_INDEX", "SYNTHESIS_CHUNK_INDEX", "CoverageDegradation"]

#: ``chunk_index`` stamped on a degradation that belongs to the whole run
#: rather than to one chunk — today only the cross-chunk synthesis pass
#: (#2269). The pass is not a chunk, so it takes a sentinel rather than
#: borrowing a real chunk's index and inflating that chunk's degradation count
#: on the #2003 surfaces. It lives beside the model, not in the synthesis
#: module, so the surfaces that must exclude it can recognize it without
#: importing the pass.
SYNTHESIS_CHUNK_INDEX = -1

#: ``chunk_index`` stamped on a degradation carried over from an earlier
#: round: a file skipped as covered this round whose coverage record says
#: only a prefix of its diff was ever reviewed (lintro-ops #37). No chunk of
#: this run read it, so it takes its own sentinel.
CARRIED_CHUNK_INDEX = -2


@dataclass(frozen=True, slots=True)
class CoverageDegradation:
    """A single chunk-level limit that may have suppressed findings.

    Attributes:
        reason: Which limit applied to the chunk.
        chunk_index: Zero-based index of the affected chunk in the run.
        split: Whether an output-exhaustion retry actually split the chunk.
            A single-file chunk cannot be split, so it is retried once
            unchanged and keeps its whole-chunk view; both paths record
            ``OUTPUT_EXHAUSTION_RETRIED``, and this is what tells them apart.
            Meaningful only for that reason, and ``True`` by default so a
            degradation recorded without it reads as the ordinary split.
        limit: The numeric bound the degradation reports, when it has one: the
            per-call turn limit for ``TURN_LIMIT_REACHED`` (#2685). Serialized
            only when set, so older records round-trip byte-identically.
        detail: Why the limit applied, when a reason has more than one cause:
            the failed question pass's kind, plus "; retried once" after a
            failed retry (#2813). Serialized only when set.
    """

    reason: CoverageDegradationReason
    chunk_index: int
    split: bool = True
    limit: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the degradation for JSON and MCP payloads.

        ``split`` is emitted only for the reason it describes, so the payload
        of every other degradation is unchanged and no consumer is handed a
        flag that means nothing for the limit it sits on.

        Returns:
            JSON-serializable mapping with the reason as a plain string.
        """
        payload: dict[str, Any] = {
            "reason": str(self.reason),
            "chunk_index": self.chunk_index,
        }
        if self.reason is CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED:
            payload["split"] = self.split

        if self.limit is not None:

            payload["limit"] = self.limit
        if self.detail:
            payload["detail"] = self.detail
        return payload
