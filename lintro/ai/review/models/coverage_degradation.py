"""One recorded coverage degradation for a review chunk (#2003)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)

__all__ = ["CoverageDegradation"]


@dataclass(frozen=True, slots=True)
class CoverageDegradation:
    """A single chunk-level limit that may have suppressed findings.

    Attributes:
        reason: Which limit applied to the chunk.
        chunk_index: Zero-based index of the affected chunk in the run.
        findings_cap: The per-call findings ceiling in force after the
            degradation was applied. For an output-exhaustion retry this is
            the *tightened* cap the retry ran under, not the original.
    """

    reason: CoverageDegradationReason
    chunk_index: int
    findings_cap: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize the degradation for JSON and MCP payloads.

        Returns:
            JSON-serializable mapping with the reason as a plain string.
        """
        return {
            "reason": str(self.reason),
            "chunk_index": self.chunk_index,
            "findings_cap": self.findings_cap,
        }
