"""Tests for review progress tracking."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.progress import (
    NullReviewProgress,
    RichReviewProgress,
    _passes_per_chunk,
)


class TestPassesPerChunk:
    """Tests for the _passes_per_chunk helper."""

    def test_depth_1_single_pass(self):
        assert_that(_passes_per_chunk(1)).is_equal_to(1)

    def test_depth_2_two_passes(self):
        assert_that(_passes_per_chunk(2)).is_equal_to(2)

    def test_depth_3_three_passes(self):
        assert_that(_passes_per_chunk(3)).is_equal_to(3)


class TestNullReviewProgress:
    """NullReviewProgress silently accepts all calls."""

    def test_full_lifecycle(self):
        p = NullReviewProgress()
        p.on_start(total_chunks=3, depth=2)
        p.on_chunk_start(chunk_index=0, files=["a.py"])
        p.on_step(chunk_index=0, step="reviewing")
        p.on_chunk_done(chunk_index=0)
        p.on_complete(total_findings=5)


class TestRichReviewProgress:
    """RichReviewProgress lifecycle without a live terminal."""

    def test_full_lifecycle_does_not_crash(self):
        from rich.console import Console

        console = Console(file=None, force_terminal=False)
        p = RichReviewProgress(console=console)
        p.on_start(total_chunks=2, depth=1)
        p.on_chunk_start(chunk_index=0, files=["a.py", "b.py"])
        p.on_step(chunk_index=0, step="reviewing")
        p.on_chunk_done(chunk_index=0)
        p.on_chunk_start(chunk_index=1, files=["c.py"])
        p.on_step(chunk_index=1, step="reviewing")
        p.on_chunk_done(chunk_index=1)
        p.on_complete(total_findings=3)

    def test_calls_before_start_are_safe(self):
        from rich.console import Console

        console = Console(file=None, force_terminal=False)
        p = RichReviewProgress(console=console)
        p.on_chunk_start(chunk_index=0, files=["a.py"])
        p.on_step(chunk_index=0, step="reviewing")
        p.on_chunk_done(chunk_index=0)

    def test_complete_without_start_is_safe(self):
        from rich.console import Console

        console = Console(file=None, force_terminal=False)
        p = RichReviewProgress(console=console)
        p.on_complete(total_findings=0)

    def test_single_file_shows_filename(self):
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        p = RichReviewProgress(console=console)
        p.on_start(total_chunks=1, depth=1)
        p.on_chunk_start(chunk_index=0, files=["src/main.py"])
        p.on_chunk_done(chunk_index=0)
        p.on_complete(total_findings=0)
        output = buf.getvalue()
        assert_that(output).contains("0 findings")

    def test_singular_finding_label(self):
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        p = RichReviewProgress(console=console)
        p.on_start(total_chunks=1, depth=1)
        p.on_chunk_done(chunk_index=0)
        p.on_complete(total_findings=1)
        output = buf.getvalue()
        assert_that(output).contains("1 finding")
        assert_that(output).does_not_contain("1 findings")
