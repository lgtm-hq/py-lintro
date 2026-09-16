"""Per-chunk queued/in-flight timing detail for a review run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChunkTiming:
    """Wall-clock split for a single chunk's provider call.

    ``queued_seconds`` is time the chunk spent waiting on the concurrency
    semaphore before any provider work started; ``in_flight_seconds`` is the
    time from acquiring the semaphore to the chunk finishing. Splitting them
    makes semaphore starvation visible: a run where queued time dominates is
    capped by ``ai.max_parallel_calls``, not by provider latency.

    ``provider_seconds`` is the main review call's own wall time, inside the
    in-flight span: the gap between the two is prompt building, parsing and
    any depth-2/3 pass. ``turns`` is the agent turn count the transport
    reported for that call, or ``None`` when the transport reports none
    (lintro-ops #37).

    Attributes:
        chunk_index (int): Position of the chunk in the run.
        files (int): Number of changed files in the chunk.
        queued_seconds (float): Seconds spent waiting for a concurrency slot.
        in_flight_seconds (float): Seconds spent reviewing once admitted.
        failed (bool): True when the chunk ended in an error or a stop.
        provider_seconds (float): Wall-clock seconds of the main provider
            call; ``0.0`` when the chunk never completed one.
        turns (int | None): Transport-reported agent turns for that call.
    """

    chunk_index: int
    files: int
    queued_seconds: float
    in_flight_seconds: float
    failed: bool = False
    provider_seconds: float = 0.0
    turns: int | None = None

    @property
    def total_seconds(self) -> float:
        """Return queued plus in-flight seconds for the chunk."""
        return self.queued_seconds + self.in_flight_seconds

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping for this chunk.

        Returns:
            Mapping with the chunk index, file count, and timing split.
        """
        return {
            "chunk_index": self.chunk_index,
            "files": self.files,
            "queued_seconds": round(self.queued_seconds, 3),
            "in_flight_seconds": round(self.in_flight_seconds, 3),
            "total_seconds": round(self.total_seconds, 3),
            "failed": self.failed,
            "provider_seconds": round(self.provider_seconds, 3),
            # ``null`` rather than absent, so a consumer sees the key on
            # every chunk and can tell "unknown" from "not emitted".
            "turns": self.turns,
        }
