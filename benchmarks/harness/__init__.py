"""Core harness components: timing math, runners, results, and detection."""

from __future__ import annotations

from benchmarks.harness.detect import CompetitorTool, detect_runners, which
from benchmarks.harness.results import (
    BenchmarkReport,
    ReportMetadata,
    ScenarioResult,
    render_markdown_table,
)
from benchmarks.harness.runners import Runner, Scenario, build_runners
from benchmarks.harness.timing import TimingStats, measure_command, summarize

__all__ = [
    "BenchmarkReport",
    "CompetitorTool",
    "ReportMetadata",
    "Runner",
    "Scenario",
    "ScenarioResult",
    "TimingStats",
    "build_runners",
    "detect_runners",
    "measure_command",
    "render_markdown_table",
    "summarize",
    "which",
]
