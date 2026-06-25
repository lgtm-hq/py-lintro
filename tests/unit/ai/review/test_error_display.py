"""Tests for review error display."""

from __future__ import annotations

from io import StringIO

from assertpy import assert_that
from rich.console import Console

from lintro.ai.exceptions import AIProviderError
from lintro.ai.review.error_display import render_review_error
from lintro.ai.review.exceptions import ReviewExecutionError


def test_render_timeout_includes_actionable_hints() -> None:
    """Timeout failures show chunk context and config hints."""
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True)
    error = ReviewExecutionError(
        message="Review aborted before all chunks completed.",
        chunk_index=5,
        total_chunks=6,
        step="reviewing",
        completed_chunks=5,
        cause_message="Cursor CLI timed out after 300s",
    )

    render_review_error(error=error, console=console)
    output = buf.getvalue()

    assert_that(output).contains("Review failed")
    assert_that(output).contains("chunk 6/6")
    assert_that(output).contains("5 chunks completed")
    assert_that(output).contains("api_timeout")
    assert_that(output).does_not_contain("Traceback")


def test_render_provider_error_without_traceback() -> None:
    """Generic provider errors render as panels, not tracebacks."""
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True)

    render_review_error(
        error=AIProviderError("Cursor CLI timed out after 300s"),
        console=console,
    )
    output = buf.getvalue()

    assert_that(output).contains("Review failed")
    assert_that(output).contains("timed out")
    assert_that(output).does_not_contain("Traceback")
