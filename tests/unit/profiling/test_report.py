"""Tests for the profiling report builder and renderer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.profiling.report import (
    build_profile_data,
    build_timings,
    render_profile_report,
)


def _issue(file: str) -> SimpleNamespace:
    """Build a minimal issue-like object with a ``file`` attribute.

    Args:
        file: The file path to attach to the issue.

    Returns:
        A lightweight object exposing ``file``.
    """
    return SimpleNamespace(file=file)


@pytest.fixture
def sample_results() -> list[ToolResult]:
    """Provide a representative set of timed tool results.

    Returns:
        A list mixing timed, skipped, and untimed (post-check-like) results.
    """
    return [
        ToolResult(
            name="ruff",
            success=True,
            issues_count=2,
            issues=[_issue("a.py"), _issue("b.py")],
            duration=0.4,
        ),
        ToolResult(
            name="mypy",
            success=False,
            issues_count=3,
            issues=[_issue("a.py"), _issue("a.py"), _issue("c.py")],
            duration=12.0,
        ),
        ToolResult(
            name="bandit",
            skipped=True,
            skip_reason="not installed",
            duration=None,
        ),
        # An untimed result (e.g. a post-check) must be excluded.
        ToolResult(name="darglint", success=True, issues_count=1, duration=None),
    ]


def test_build_timings_sorted_slowest_first(
    sample_results: list[ToolResult],
) -> None:
    """Timings are ordered by descending duration."""
    timings = build_timings(sample_results)

    assert_that([t.tool for t in timings]).is_equal_to(["mypy", "ruff"])


def test_build_timings_excludes_skipped_and_untimed(
    sample_results: list[ToolResult],
) -> None:
    """Skipped tools and untimed results are omitted from the profile."""
    tools = [t.tool for t in build_timings(sample_results)]

    assert_that(tools).does_not_contain("bandit")
    assert_that(tools).does_not_contain("darglint")


def test_build_timings_attributes_files_and_issues(
    sample_results: list[ToolResult],
) -> None:
    """Per-tool file counts dedupe by path; issue counts mirror the result."""
    timings = {t.tool: t for t in build_timings(sample_results)}

    assert_that(timings["mypy"].files_checked).is_equal_to(2)
    assert_that(timings["mypy"].issues_found).is_equal_to(3)
    assert_that(timings["ruff"].files_checked).is_equal_to(2)


def test_build_profile_data_shape(sample_results: list[ToolResult]) -> None:
    """The JSON payload exposes total_duration, tools, and suggestions."""
    data = build_profile_data(sample_results)

    assert_that(data).contains_key("total_duration", "tools", "suggestions")
    assert_that(data["total_duration"]).is_equal_to(12.4)
    assert_that(data["tools"]).is_length(2)
    first = data["tools"][0]
    assert_that(first).contains_key(
        "name",
        "duration",
        "files_checked",
        "issues_found",
    )
    assert_that(first["name"]).is_equal_to("mypy")


def test_build_profile_data_empty_when_nothing_timed() -> None:
    """No timed results yields a zero total and empty tool list."""
    results = [
        ToolResult(
            name="bandit",
            skipped=True,
            skip_reason="not installed",
        ),
    ]

    data = build_profile_data(results)

    assert_that(data["total_duration"]).is_equal_to(0)
    assert_that(data["tools"]).is_empty()
    assert_that(data["suggestions"]).is_empty()


def test_render_profile_report_contains_table_and_total(
    sample_results: list[ToolResult],
) -> None:
    """The rendered report includes the header, tools, and TOTAL row."""
    report = render_profile_report(sample_results)

    assert_that(report).contains("Performance Profile")
    assert_that(report).contains("mypy")
    assert_that(report).contains("ruff")
    assert_that(report).contains("TOTAL")
    assert_that(report).contains("12.00s")


def test_render_profile_report_empty_without_timings() -> None:
    """Rendering with no timed tools returns an empty string."""
    report = render_profile_report(
        [ToolResult(name="x", skipped=True, skip_reason="skip")],
    )

    assert_that(report).is_equal_to("")
