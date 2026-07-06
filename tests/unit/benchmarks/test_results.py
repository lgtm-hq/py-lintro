"""Unit tests for benchmark result serialization and rendering."""

from __future__ import annotations

from assertpy import assert_that

from benchmarks.harness.results import (
    BenchmarkReport,
    ReportMetadata,
    ScenarioResult,
    render_markdown_table,
)
from benchmarks.harness.timing import summarize


def _make_result(
    *,
    tool: str,
    label: str,
    scenario: str,
    samples: list[float],
) -> ScenarioResult:
    """Build a ScenarioResult for tests.

    Args:
        tool: Tool identifier.
        label: Human-readable label.
        scenario: Scenario identifier.
        samples: Per-run durations in seconds.

    Returns:
        ScenarioResult: A populated result.
    """
    return ScenarioResult(
        tool=tool,
        label=label,
        fixture="small-python",
        scenario=scenario,
        stats=summarize(samples),
        exit_codes=[0] * len(samples),
        command=["uv", "run", "lintro", "chk", "."],
    )


def _make_report() -> BenchmarkReport:
    """Build a two-tool report for tests.

    Returns:
        BenchmarkReport: Report with a lintro baseline and a slower competitor.
    """
    metadata = ReportMetadata(
        generated_at="2026-07-06T00:00:00+00:00",
        git_sha="abc1234",
        platform="TestOS",
        python_version="3.13.0",
        cpu_count=8,
        runs=3,
        notes=["skipped pre_commit: not installed"],
    )
    results = [
        _make_result(
            tool="lintro",
            label="lintro",
            scenario="full_check_cold",
            samples=[0.50, 0.52, 0.51],
        ),
        _make_result(
            tool="megalinter",
            label="megalinter",
            scenario="full_check_cold",
            samples=[1.00, 1.04, 1.02],
        ),
    ]
    return BenchmarkReport(metadata=metadata, results=results)


def test_report_json_round_trip_is_lossless() -> None:
    """Serializing then parsing a report preserves all data."""
    report = _make_report()

    restored = BenchmarkReport.from_json(report.to_json())

    assert_that(restored.metadata.git_sha).is_equal_to("abc1234")
    assert_that(restored.metadata.notes).contains("skipped pre_commit: not installed")
    assert_that(restored.results).is_length(2)
    assert_that(restored.results[0].tool).is_equal_to("lintro")
    assert_that(restored.results[0].stats.median_s).is_equal_to(0.51)
    assert_that(restored.results[1].command).is_equal_to(
        ["uv", "run", "lintro", "chk", "."],
    )


def test_report_from_dict_rejects_unknown_schema() -> None:
    """A mismatched schema version is rejected."""
    data = _make_report().to_dict()
    data["schema_version"] = 999

    assert_that(BenchmarkReport.from_dict).raises(ValueError).when_called_with(data)


def test_scenario_result_to_dict_has_expected_keys() -> None:
    """The serialized scenario result exposes the stable public keys."""
    result = _make_result(
        tool="lintro",
        label="lintro",
        scenario="full_check_warm",
        samples=[0.4, 0.5],
    )

    payload = result.to_dict()

    assert_that(payload).contains_key(
        "tool",
        "label",
        "fixture",
        "scenario",
        "stats",
        "exit_codes",
        "command",
    )
    assert_that(payload["stats"]).contains_key("median_s", "samples")


def test_render_markdown_table_marks_baseline_relative() -> None:
    """The baseline tool renders at 1.00x and the competitor at 2.00x."""
    table = render_markdown_table(_make_report())

    assert_that(table).contains("| Fixture | Scenario | Tool |")
    assert_that(table).contains("1.00x")
    assert_that(table).contains("2.00x")
    assert_that(table).contains("megalinter")


def test_render_markdown_table_empty_report() -> None:
    """An empty report still renders a header and a placeholder row."""
    metadata = ReportMetadata(
        generated_at="2026-07-06T00:00:00+00:00",
        git_sha="abc1234",
        platform="TestOS",
        python_version="3.13.0",
        cpu_count=8,
        runs=0,
        notes=[],
    )
    table = render_markdown_table(BenchmarkReport(metadata=metadata, results=[]))

    assert_that(table).contains("no results")


def test_write_json_creates_file(tmp_path) -> None:
    """Writing a report creates a parseable JSON file on disk."""
    report = _make_report()
    destination = tmp_path / "nested" / "report.json"

    report.write_json(destination)

    assert_that(destination.exists()).is_true()
    parsed = BenchmarkReport.from_json(destination.read_text(encoding="utf-8"))
    assert_that(parsed.results).is_length(2)
