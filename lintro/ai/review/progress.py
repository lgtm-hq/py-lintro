"""Progress tracking for AI review operations.

Provides a protocol for progress callbacks and a Rich terminal
implementation with a live progress bar, elapsed timer, and
per-step status updates.
"""

from __future__ import annotations

from typing import Protocol

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)


class ReviewProgressCallback(Protocol):
    """Protocol for review progress reporting.

    Implementors receive lifecycle calls from the orchestrator to
    update a progress display. The default no-op implementation
    (``NullReviewProgress``) silently ignores all calls.
    """

    def on_start(self, *, total_chunks: int, depth: int) -> None:
        """Called once when the review begins.

        Args:
            total_chunks: Number of semantic chunks to review.
            depth: Review depth level (1-3).
        """
        ...

    def on_chunk_start(self, *, chunk_index: int, files: list[str]) -> None:
        """Called when a chunk begins processing.

        Args:
            chunk_index: Zero-based chunk index.
            files: List of file paths in this chunk.
        """
        ...

    def on_step(self, *, chunk_index: int, step: str) -> None:
        """Called when a sub-step begins within a chunk.

        Args:
            chunk_index: Zero-based chunk index.
            step: Human-readable step name (e.g. "reviewing",
                "generating questions", "adversarial sweep").
        """
        ...

    def on_chunk_done(self, *, chunk_index: int) -> None:
        """Called when a chunk finishes processing.

        Args:
            chunk_index: Zero-based chunk index.
        """
        ...

    def on_complete(self, *, total_findings: int) -> None:
        """Called when the entire review is finished.

        Args:
            total_findings: Number of findings produced.
        """
        ...

    def on_abort(self) -> None:
        """Called when the review stops before completing successfully."""
        ...


class NullReviewProgress:
    """No-op progress tracker (silent)."""

    def on_start(self, *, total_chunks: int, depth: int) -> None:
        """Ignore review start notification."""

    def on_chunk_start(self, *, chunk_index: int, files: list[str]) -> None:
        """Ignore chunk start notification."""

    def on_step(self, *, chunk_index: int, step: str) -> None:
        """Ignore step notification."""

    def on_chunk_done(self, *, chunk_index: int) -> None:
        """Ignore chunk completion notification."""

    def on_complete(self, *, total_findings: int) -> None:
        """Ignore review completion notification."""

    def on_abort(self) -> None:
        """Ignore review abort notification."""


class RichReviewProgress:
    """Rich-based live progress bar for terminal output.

    Displays a progress bar with:
    - Spinner animation
    - Current step description
    - Chunk progress (e.g. 2/3)
    - Elapsed time
    """

    def __init__(self, *, console: Console | None = None) -> None:
        """Initialize the Rich progress display.

        Args:
            console: Optional Rich console; defaults to stdout.
        """
        self._console = console or Console()
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None
        self._total_chunks = 0
        self._depth = 1

    def on_start(self, *, total_chunks: int, depth: int) -> None:
        """Start the live progress bar for the review run."""
        passes = _passes_per_chunk(depth)
        depth_label = {1: "standard", 2: "thorough", 3: "deep"}.get(depth, "")
        self._total_chunks = total_chunks
        self._depth = depth
        pass_word = "passes" if passes > 1 else "pass"

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=30),
            MofNCompleteColumn(),
            TextColumn("[dim]chunks[/dim]"),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
        )
        self._task_id = self._progress.add_task(
            f"Reviewing ({depth_label}, {passes} {pass_word}/chunk)",
            total=total_chunks,
        )
        self._progress.start()

    def on_chunk_start(self, *, chunk_index: int, files: list[str]) -> None:
        """Update the bar description for a new chunk."""
        if self._progress is None or self._task_id is None:
            return
        file_count = len(files)
        label = files[0] if file_count == 1 else f"{file_count} files"
        self._progress.update(
            self._task_id,
            description=f"Chunk {chunk_index + 1}/{self._total_chunks}: {label}",
        )

    def on_step(self, *, chunk_index: int, step: str) -> None:
        """Update the bar description for an in-chunk step."""
        if self._progress is None or self._task_id is None:
            return
        self._progress.update(
            self._task_id,
            description=(f"Chunk {chunk_index + 1}/{self._total_chunks}: {step}"),
        )

    def on_chunk_done(self, *, chunk_index: int) -> None:  # noqa: ARG002
        """Advance the bar after a chunk completes."""
        if self._progress is None or self._task_id is None:
            return
        self._progress.update(self._task_id, advance=1)

    def on_complete(self, *, total_findings: int) -> None:
        """Stop the bar and print the completion summary."""
        self._stop_progress()
        noun = "finding" if total_findings == 1 else "findings"
        self._console.print(
            f"[bold green]✓[/bold green] Review complete — {total_findings} {noun}",
        )

    def on_abort(self) -> None:
        """Stop the bar without printing a completion summary."""
        self._stop_progress()

    def _stop_progress(self) -> None:
        """Stop the live progress display if it is running."""
        if self._progress is not None:
            self._progress.stop()
            self._progress = None


def _passes_per_chunk(depth: int) -> int:
    """Calculate the number of AI calls per chunk at the given depth."""
    if depth >= 3:
        return 3  # questions + review + adversarial
    if depth >= 2:
        return 2  # questions + review
    return 1  # review only
