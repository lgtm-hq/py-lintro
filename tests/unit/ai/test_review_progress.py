"""Tests for review progress tracking."""

from __future__ import annotations

from io import StringIO

from assertpy import assert_that
from rich.console import Console

from lintro.ai.review.progress import (
    NullReviewProgress,
    RichReviewProgress,
    _passes_per_chunk,
)
from lintro.parsers.base_parser import strip_ansi_codes

# -- _passes_per_chunk -----------------------------------------------------


def test_passes_per_chunk_depth_1_single_pass() -> None:
    """Depth 1 runs one AI pass per chunk."""
    assert_that(_passes_per_chunk(1)).is_equal_to(1)


def test_passes_per_chunk_depth_2_two_passes() -> None:
    """Depth 2 runs two AI passes per chunk."""
    assert_that(_passes_per_chunk(2)).is_equal_to(2)


def test_passes_per_chunk_depth_3_three_passes() -> None:
    """Depth 3 runs three AI passes per chunk."""
    assert_that(_passes_per_chunk(3)).is_equal_to(3)


# -- NullReviewProgress ----------------------------------------------------


def test_null_review_progress_full_lifecycle() -> None:
    """All lifecycle hooks are safe no-ops."""
    progress = NullReviewProgress()
    progress.on_start(total_chunks=3, depth=2)
    progress.on_chunk_start(chunk_index=0, files=["a.py"])
    progress.on_step(chunk_index=0, step="reviewing")
    progress.on_chunk_done(chunk_index=0)
    progress.on_complete(total_findings=5)
    progress.on_abort()


# -- RichReviewProgress ----------------------------------------------------


def test_rich_review_progress_full_lifecycle_does_not_crash() -> None:
    """Exercise the full progress lifecycle without raising."""
    console = Console(file=None, force_terminal=False)
    progress = RichReviewProgress(console=console)
    progress.on_start(total_chunks=2, depth=1)
    progress.on_chunk_start(chunk_index=0, files=["a.py", "b.py"])
    progress.on_step(chunk_index=0, step="reviewing")
    progress.on_chunk_done(chunk_index=0)
    progress.on_chunk_start(chunk_index=1, files=["c.py"])
    progress.on_step(chunk_index=1, step="reviewing")
    progress.on_chunk_done(chunk_index=1)
    progress.on_complete(total_findings=3)


def test_rich_review_progress_calls_before_start_are_safe() -> None:
    """Chunk callbacks before on_start do not raise."""
    console = Console(file=None, force_terminal=False)
    progress = RichReviewProgress(console=console)
    progress.on_chunk_start(chunk_index=0, files=["a.py"])
    progress.on_step(chunk_index=0, step="reviewing")
    progress.on_chunk_done(chunk_index=0)


def test_rich_review_progress_complete_without_start_is_safe() -> None:
    """on_complete before on_start does not raise."""
    console = Console(file=None, force_terminal=False)
    progress = RichReviewProgress(console=console)
    progress.on_complete(total_findings=0)


def test_rich_review_progress_abort_stops_without_completion_message() -> None:
    """on_abort stops the bar without printing the success summary."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120)
    progress = RichReviewProgress(console=console)
    progress.on_start(total_chunks=1, depth=1)
    progress.on_abort()
    output = strip_ansi_codes(buf.getvalue())

    assert_that(output).does_not_contain("Review complete")


def test_rich_review_progress_single_file_shows_filename() -> None:
    """Single-file chunks render the plural findings label."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120)
    progress = RichReviewProgress(console=console)
    progress.on_start(total_chunks=1, depth=1)
    progress.on_chunk_start(chunk_index=0, files=["src/main.py"])
    progress.on_chunk_done(chunk_index=0)
    progress.on_complete(total_findings=0)
    output = strip_ansi_codes(buf.getvalue())

    assert_that(output).contains("0 findings")


def test_rich_review_progress_singular_finding_label() -> None:
    """Exactly one finding uses the singular noun."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120)
    progress = RichReviewProgress(console=console)
    progress.on_start(total_chunks=1, depth=1)
    progress.on_chunk_done(chunk_index=0)
    progress.on_complete(total_findings=1)
    output = strip_ansi_codes(buf.getvalue())

    assert_that(output).contains("1 finding")
    assert_that(output).does_not_contain("1 findings")
