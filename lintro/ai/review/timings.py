"""Per-phase timing instrumentation for ``lintro review`` (issue #2148).

The orchestrator only *records* spans through :class:`ReviewTimingRecorder`;
every value object and every rendering decision lives here so the orchestrator
does not grow further. Instrumentation is always on and deliberately cheap: a
:func:`time.monotonic` read per span plus one list append.

Nothing recorded here may influence review content. Timings never reach prompt
text, finding text, the readiness verdict, convergence checks, or the persisted
sticky state blob — they are surfaced only in the review JSON payload, the
terminal summary line, and the posted run-mechanics footer.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum

from lintro.ai.review.models.chunk_timing import ChunkTiming
from lintro.ai.review.models.phase_span import PhaseSpan
from lintro.ai.review.models.review_timings import ReviewTimings

__all__ = [
    "ChunkTiming",
    "PhaseSpan",
    "ReviewPhase",
    "ReviewTimingRecorder",
    "ReviewTimings",
    "format_duration",
    "format_timing_summary",
]


class ReviewPhase(StrEnum):
    """Stable phase identifiers recorded during a review run.

    ``validation`` is the post-merge tail: provider session teardown and
    progress callbacks, then the work that decides what survives
    (context-finding rejection, coverage and resume bookkeeping, flag
    reconciliation), through to the assembled result.

    ``provider`` is an envelope: it spans the whole chunk fan-out, including
    the per-chunk ``generated_questions`` (depth >= 2) and ``adversarial``
    (depth >= 3) spans recorded inside it. Phase sums therefore exceed the run
    total whenever those nested phases run, concurrency or not.

    ``lintro review`` posts its GitHub comment *after* the result is rendered,
    so posting is outside the orchestrator's measured window and has no phase
    here; ``duration_seconds`` and these spans describe the review itself.
    """

    CONTEXT_COLLECTION = "context_collection"
    CHUNKING = "chunking"
    RESUME_PLANNING = "resume_planning"
    PROVIDER = "provider"
    GENERATED_QUESTIONS = "generated_questions"
    ADVERSARIAL = "adversarial"
    PARSE_MERGE = "parse_merge"
    VALIDATION = "validation"


# Short labels for the one-line terminal/footer summary. Phases absent from
# this map fall back to their identifier.
_SUMMARY_LABELS: dict[str, str] = {
    ReviewPhase.CONTEXT_COLLECTION: "context",
    ReviewPhase.PARSE_MERGE: "merge",
    ReviewPhase.RESUME_PLANNING: "resume",
    ReviewPhase.GENERATED_QUESTIONS: "questions",
}

# Phases recorded *inside* the provider envelope. ``provider`` is the wall
# clock of the whole chunk fan-out, so these nested spans are already counted
# in it; the summary lists them within the provider parenthetical rather than
# ranking a superset beside its own parts.
_NESTED_IN_PROVIDER: frozenset[str] = frozenset(
    {ReviewPhase.GENERATED_QUESTIONS, ReviewPhase.ADVERSARIAL},
)


class ReviewTimingRecorder:
    """Collect monotonic phase spans and per-chunk timings for one run.

    The recorder is mutable by design: it is threaded through the orchestrator's
    async chunk fan-out and mutated from the event loop, then frozen into a
    :class:`ReviewTimings` value object once the run finishes. All mutation
    happens on a single event loop, so no locking is required.
    """

    def __init__(self, *, started_at: float | None = None) -> None:
        """Initialize an empty recorder.

        Args:
            started_at: Optional monotonic start stamp. Defaults to now.
        """
        self._started_at: float = (
            started_at if started_at is not None else time.monotonic()
        )
        self._phases: dict[str, PhaseSpan] = {}
        self._chunks: list[ChunkTiming] = []

    @property
    def started_at(self) -> float:
        """Return the monotonic stamp the run started at."""
        return self._started_at

    @property
    def elapsed_seconds(self) -> float:
        """Return wall-clock seconds since the recorder was created."""
        return max(time.monotonic() - self._started_at, 0.0)

    def add_phase(self, *, name: str, seconds: float) -> None:
        """Accumulate seconds into a named phase.

        Repeat calls fold into the existing span and bump its occurrence
        count; the phase keeps its first-occurrence position.

        Args:
            name: Phase identifier.
            seconds: Wall-clock seconds to add. Negative values clamp to zero.
        """
        key = str(name)
        existing = self._phases.get(key)
        added = max(seconds, 0.0)
        if existing is None:
            self._phases[key] = PhaseSpan(name=key, seconds=added, occurrences=1)
            return
        self._phases[key] = PhaseSpan(
            name=key,
            seconds=existing.seconds + added,
            occurrences=existing.occurrences + 1,
        )

    @contextmanager
    def phase(self, *, name: str) -> Iterator[None]:
        """Record the wall-clock duration of the wrapped block as a phase.

        The span is recorded even when the block raises, so a run that stops
        early (cost cap, timeout, SIGTERM) still reports where its time went.

        Args:
            name: Phase identifier.

        Yields:
            None: While the timed block runs.
        """
        started = time.monotonic()
        try:
            yield
        finally:
            self.add_phase(name=name, seconds=time.monotonic() - started)

    def add_chunk(
        self,
        *,
        chunk_index: int,
        files: int,
        queued_seconds: float,
        in_flight_seconds: float,
        failed: bool = False,
    ) -> None:
        """Record one chunk's queued/in-flight split.

        Args:
            chunk_index: Position of the chunk in the run.
            files: Number of changed files in the chunk.
            queued_seconds: Seconds spent waiting on the concurrency semaphore.
            in_flight_seconds: Seconds spent reviewing once admitted.
            failed: True when the chunk ended in an error or a stop.
        """
        self._chunks.append(
            ChunkTiming(
                chunk_index=chunk_index,
                files=files,
                queued_seconds=max(queued_seconds, 0.0),
                in_flight_seconds=max(in_flight_seconds, 0.0),
                failed=failed,
            ),
        )

    def build(
        self,
        *,
        total_seconds: float | None = None,
        max_parallel: int = 1,
    ) -> ReviewTimings:
        """Freeze the recorded spans into an immutable breakdown.

        Args:
            total_seconds: Wall-clock seconds for the run. Defaults to the
                recorder's own elapsed time.
            max_parallel: Effective concurrency ceiling for chunk calls.

        Returns:
            The immutable timing breakdown for this run.
        """
        elapsed = self.elapsed_seconds if total_seconds is None else total_seconds
        return ReviewTimings(
            total_seconds=max(elapsed, 0.0),
            phases=tuple(self._phases.values()),
            chunks=tuple(sorted(self._chunks, key=lambda item: item.chunk_index)),
            max_parallel=max(max_parallel, 1),
        )


def format_duration(*, seconds: float) -> str:
    """Format a duration as a compact human-readable string.

    Sub-minute durations keep one decimal so a fast phase does not collapse to
    ``0s``; longer ones round to whole seconds and gain minute/hour parts.

    Args:
        seconds: Duration in seconds. Negative values clamp to zero.

    Returns:
        A string such as ``0.4s``, ``22.0s``, ``4m52s``, or ``1h04m52s``.
    """
    total = max(seconds, 0.0)
    if total < 60.0:
        return f"{total:.1f}s"
    whole = int(round(total))
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"


def format_timing_summary(*, timings: ReviewTimings) -> str:
    """Render the one-line per-phase timing summary.

    Phases are ordered by descending duration so the dominant phase reads
    first — the question this instrumentation exists to answer. The provider
    phase carries the chunk count and the concurrency ceiling inline, followed
    by the depth phases nested inside it, so an envelope is never ranked as a
    peer of its own parts.

    Args:
        timings: Timing breakdown for the run.

    Returns:
        A line such as ``total 4m52s — provider 4m10s (7 chunks, max parallel
        5), context 22.0s, merge 8.0s``. When no phase was recorded, only the
        total is returned.
    """
    total = f"total {format_duration(seconds=timings.total_seconds)}"
    ordered = sorted(
        enumerate(timings.phases),
        key=lambda item: (-item[1].seconds, item[0]),
    )
    nested = [
        f"{_SUMMARY_LABELS.get(span.name, span.name)} "
        f"{format_duration(seconds=span.seconds)}"
        for _position, span in ordered
        if span.name in _NESTED_IN_PROVIDER and span.seconds > 0.0
    ]
    parts: list[str] = []
    for _position, span in ordered:
        if span.name in _NESTED_IN_PROVIDER:
            continue
        is_provider = span.name == ReviewPhase.PROVIDER
        if span.seconds <= 0.0 and not is_provider:
            continue
        label = _SUMMARY_LABELS.get(span.name, span.name)
        part = f"{label} {format_duration(seconds=span.seconds)}"
        if is_provider:
            detail: list[str] = []
            if timings.chunks:
                chunk_word = "chunk" if len(timings.chunks) == 1 else "chunks"
                detail.append(f"{len(timings.chunks)} {chunk_word}")
                detail.append(f"max parallel {timings.max_parallel}")
            detail.extend(nested)
            if detail:
                part += f" ({', '.join(detail)})"
        parts.append(part)
    if not parts:
        return total
    return f"{total} — {', '.join(parts)}"
