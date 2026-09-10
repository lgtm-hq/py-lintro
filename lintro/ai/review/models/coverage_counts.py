"""Per-round coverage counters for history and the sticky (#2154)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lintro.ai.review.models._coerce import coerce_int

__all__ = ["CoverageCounts"]


@dataclass(frozen=True, slots=True)
class CoverageCounts:
    """How this round treated the review-eligible file set.

    Attributes:
        reviewed: Files the provider actually read this round.
        carried: Unchanged covered files inherited at zero cost.
        awaiting: Eligible files not yet covered at HEAD.
        invalidated: Covered files re-queued (group/import/flag).
        eligible: Review-eligible denominator (100% = this count).
    """

    reviewed: int = 0
    carried: int = 0
    awaiting: int = 0
    invalidated: int = 0
    eligible: int = 0

    @property
    def covered_at_head(self) -> int:
        """Return files whose current hash is covered after this round."""
        return max(self.eligible - self.awaiting, 0)

    @property
    def complete(self) -> bool:
        """Return whether every eligible file is covered at HEAD."""
        return self.awaiting == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the counters.

        Returns:
            JSON-serializable mapping.
        """
        return {
            "reviewed": self.reviewed,
            "carried": self.carried,
            "awaiting": self.awaiting,
            "invalidated": self.invalidated,
            "eligible": self.eligible,
            "covered_at_head": self.covered_at_head,
            "complete": self.complete,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> CoverageCounts:
        """Parse counters from untrusted JSON.

        Args:
            payload: Decoded mapping, or ``None``.

        Returns:
            Parsed counters; missing keys become zero.
        """
        data = payload or {}
        return cls(
            reviewed=coerce_int(data.get("reviewed")),
            carried=coerce_int(data.get("carried")),
            awaiting=coerce_int(data.get("awaiting")),
            invalidated=coerce_int(data.get("invalidated")),
            eligible=coerce_int(data.get("eligible")),
        )
