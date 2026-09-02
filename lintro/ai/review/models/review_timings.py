"""Per-phase timing breakdown for a review run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lintro.ai.review.models.chunk_timing import ChunkTiming
from lintro.ai.review.models.phase_span import PhaseSpan


@dataclass(frozen=True, slots=True)
class ReviewTimings:
    """Complete per-phase timing breakdown for one ``lintro review`` run.

    This is instrumentation only: nothing here feeds finding content, the
    readiness verdict, convergence, or state matching (issue #2148).

    Attributes:
        total_seconds (float): Wall-clock seconds for the whole review run,
            including the caller's context collection, through to the
            orchestrator's return.
        phases (tuple[PhaseSpan, ...]): Phase spans in first-occurrence
            order, so the sequence reads chronologically.
        chunks (tuple[ChunkTiming, ...]): Per-chunk queued/in-flight detail,
            ordered by chunk index.
        max_parallel (int): Effective concurrency ceiling applied to chunk
            provider calls for this run.
    """

    total_seconds: float = 0.0
    phases: tuple[PhaseSpan, ...] = field(default_factory=tuple)
    chunks: tuple[ChunkTiming, ...] = field(default_factory=tuple)
    max_parallel: int = 1

    def phase_seconds(self, *, name: str) -> float:
        """Return accumulated seconds for a phase, or ``0.0`` when absent.

        Args:
            name: Phase identifier to look up.

        Returns:
            Accumulated wall-clock seconds for the phase.
        """
        return next(
            (span.seconds for span in self.phases if span.name == name),
            0.0,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of the timing breakdown.

        Returns:
            Mapping with the run total, ordered phase spans, per-chunk
            detail, and the effective concurrency ceiling.
        """
        return {
            "total_seconds": round(self.total_seconds, 3),
            "max_parallel": self.max_parallel,
            "phases": [span.to_dict() for span in self.phases],
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }
