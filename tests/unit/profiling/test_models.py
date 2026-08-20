"""Tests for the profiling ToolTiming record."""

from assertpy import assert_that

from lintro.profiling.models import ToolTiming


def test_tool_timing_defaults() -> None:
    """ToolTiming defaults files_checked and issues_found to zero."""
    timing = ToolTiming(tool="ruff", duration=1.5)

    assert_that(timing.tool).is_equal_to("ruff")
    assert_that(timing.duration).is_equal_to(1.5)
    assert_that(timing.files_checked).is_equal_to(0)
    assert_that(timing.issues_found).is_equal_to(0)
