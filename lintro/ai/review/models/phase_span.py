"""Wall-clock span for one phase of a review run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PhaseSpan:
    """Accumulated wall-clock time spent in one named review phase.

    Phases are recorded in the order they first occur, so a serialized span
    list reads chronologically. ``seconds`` accumulates every occurrence of
    the phase: under chunk fan-out the per-chunk phases (question generation,
    the provider call, the adversarial sweep) overlap, so their sums can
    exceed the run's wall-clock total. That is intentional — the sum answers
    "how much provider work happened", while ``ReviewTimings.total_seconds``
    answers "how long did the user wait".

    Attributes:
        name (str): Stable phase identifier (e.g. ``provider``).
        seconds (float): Accumulated wall-clock seconds for the phase.
        occurrences (int): Number of recorded spans folded into ``seconds``.
    """

    name: str
    seconds: float
    occurrences: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping for this span.

        Returns:
            Mapping with the phase name, seconds, and occurrence count.
        """
        return {
            "name": self.name,
            "seconds": round(self.seconds, 3),
            "occurrences": self.occurrences,
        }
