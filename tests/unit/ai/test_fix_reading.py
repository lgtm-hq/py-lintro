"""Tests for _read_file_safely and _extract_context."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.fix import (
    _extract_context,
    _read_file_safely,
)

# ---------------------------------------------------------------------------
# _read_file_safely
# ---------------------------------------------------------------------------


def test_read_file_safely_reads_existing_file(tmp_path):
    """Existing file contents are returned as a string."""
    f = tmp_path / "test.py"
    f.write_text("hello world")
    result = _read_file_safely(str(f))
    assert_that(result).is_equal_to("hello world")


def test_read_file_safely_returns_none_for_missing():
    """Missing file returns None instead of raising."""
    result = _read_file_safely("/nonexistent/file.py")
    assert_that(result).is_none()


# ---------------------------------------------------------------------------
# _extract_context
# ---------------------------------------------------------------------------


def test_extract_context_extracts_context():
    """Context window is centred on the target line."""
    content = "\n".join(f"line {i}" for i in range(1, 31))
    context, start, end = _extract_context(content, 15, 5)
    assert_that(start).is_equal_to(10)
    assert_that(end).is_equal_to(20)
    assert_that(context).contains("line 15")


def test_extract_context_clamps_to_start():
    """Verify context window clamps to the first line when target is near the start."""
    content = "\n".join(f"line {i}" for i in range(1, 11))
    context, start, end = _extract_context(content, 1, 5)
    assert_that(start).is_equal_to(1)


def test_extract_context_clamps_to_end():
    """Verify context window clamps to the last line when target is near the end."""
    content = "\n".join(f"line {i}" for i in range(1, 11))
    context, start, end = _extract_context(content, 10, 5)
    assert_that(end).is_equal_to(10)
