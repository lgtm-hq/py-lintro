"""Result models, JSON (de)serialization, and markdown rendering.

The report format is intentionally simple and stable so that generated results
can be committed to ``benchmarks/results/`` and diffed across runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from benchmarks.harness.timing import TimingStats

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReportMetadata:
    """Environment metadata captured alongside benchmark results.

    Attributes:
        generated_at: ISO-8601 UTC timestamp when the report was produced.
        git_sha: Short git SHA of the checkout the run was taken from.
        platform: Platform string (e.g. ``Darwin-27.0.0-arm64``).
        python_version: Python version used to drive the harness.
        cpu_count: Logical CPU count of the host.
        runs: Number of measured runs per scenario.
        notes: Free-form notes (e.g. skipped competitors).
    """

    generated_at: str
    git_sha: str
    platform: str
    python_version: str
    cpu_count: int
    runs: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Serialize metadata to a JSON-compatible dictionary.

        Returns:
            dict[str, object]: Mapping of field names to values.
        """
        return {
            "generated_at": self.generated_at,
            "git_sha": self.git_sha,
            "platform": self.platform,
            "python_version": self.python_version,
            "cpu_count": self.cpu_count,
            "runs": self.runs,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ReportMetadata:
        """Reconstruct metadata from a dictionary.

        Args:
            data: Mapping previously produced by :meth:`to_dict`.

        Returns:
            ReportMetadata: The reconstructed metadata.
        """
        return cls(
            generated_at=str(data["generated_at"]),
            git_sha=str(data["git_sha"]),
            platform=str(data["platform"]),
            python_version=str(data["python_version"]),
            cpu_count=int(data["cpu_count"]),  # type: ignore[arg-type]
            runs=int(data["runs"]),  # type: ignore[arg-type]
            notes=list(data.get("notes", [])),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Timing result for one tool running one scenario on one fixture.

    Attributes:
        tool: Competitor tool identifier (e.g. ``lintro``).
        label: Human-readable label for the runner.
        fixture: Fixture project name.
        scenario: Scenario identifier (e.g. ``full_check_cold``).
        stats: Timing statistics for the measured runs.
        exit_codes: Exit code observed for each measured run.
        command: The argv that was executed (for reproducibility).
    """

    tool: str
    label: str
    fixture: str
    scenario: str
    stats: TimingStats
    exit_codes: list[int]
    command: list[str]

    def to_dict(self) -> dict[str, object]:
        """Serialize the scenario result to a JSON-compatible dictionary.

        Returns:
            dict[str, object]: Mapping of field names to values.
        """
        return {
            "tool": self.tool,
            "label": self.label,
            "fixture": self.fixture,
            "scenario": self.scenario,
            "stats": self.stats.to_dict(),
            "exit_codes": list(self.exit_codes),
            "command": list(self.command),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ScenarioResult:
        """Reconstruct a scenario result from a dictionary.

        Args:
            data: Mapping previously produced by :meth:`to_dict`.

        Returns:
            ScenarioResult: The reconstructed result.
        """
        stats_data: dict[str, object] = dict(data["stats"])  # type: ignore[arg-type]
        stats = TimingStats(
            runs=int(stats_data["runs"]),  # type: ignore[arg-type]
            min_s=float(stats_data["min_s"]),  # type: ignore[arg-type]
            max_s=float(stats_data["max_s"]),  # type: ignore[arg-type]
            mean_s=float(stats_data["mean_s"]),  # type: ignore[arg-type]
            median_s=float(stats_data["median_s"]),  # type: ignore[arg-type]
            stddev_s=float(stats_data["stddev_s"]),  # type: ignore[arg-type]
            samples=[float(value) for value in stats_data.get("samples", [])],  # type: ignore[union-attr]
        )
        return cls(
            tool=str(data["tool"]),
            label=str(data["label"]),
            fixture=str(data["fixture"]),
            scenario=str(data["scenario"]),
            stats=stats,
            exit_codes=[int(code) for code in data["exit_codes"]],  # type: ignore[union-attr]
            command=[str(part) for part in data["command"]],  # type: ignore[union-attr]
        )


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """A complete benchmark report: metadata plus per-scenario results.

    Attributes:
        metadata: Environment metadata for the run.
        results: All scenario results collected during the run.
    """

    metadata: ReportMetadata
    results: list[ScenarioResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Serialize the full report to a JSON-compatible dictionary.

        Returns:
            dict[str, object]: Mapping with schema version, metadata, results.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "metadata": self.metadata.to_dict(),
            "results": [result.to_dict() for result in self.results],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BenchmarkReport:
        """Reconstruct a report from a dictionary.

        Args:
            data: Mapping previously produced by :meth:`to_dict`.

        Returns:
            BenchmarkReport: The reconstructed report.

        Raises:
            ValueError: If the schema version is unsupported.
        """
        version = int(data.get("schema_version", 0))  # type: ignore[arg-type]
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {version}; expected {SCHEMA_VERSION}",
            )
        return cls(
            metadata=ReportMetadata.from_dict(dict(data["metadata"])),  # type: ignore[arg-type]
            results=[
                ScenarioResult.from_dict(dict(item))  # type: ignore[arg-type]
                for item in data["results"]  # type: ignore[union-attr]
            ],
        )

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the report to a JSON string.

        Args:
            indent: Indentation level for pretty-printing.

        Returns:
            str: JSON-encoded report.
        """
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    @classmethod
    def from_json(cls, text: str) -> BenchmarkReport:
        """Parse a report from a JSON string.

        Args:
            text: JSON produced by :meth:`to_json`.

        Returns:
            BenchmarkReport: The parsed report.
        """
        return cls.from_dict(json.loads(text))

    def write_json(self, path: Path) -> None:
        """Write the report as JSON to ``path``.

        Args:
            path: Destination file path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")


def _format_seconds(value: float) -> str:
    """Format a duration in seconds with fixed precision.

    Args:
        value: Duration in seconds.

    Returns:
        str: Formatted duration (e.g. ``1.234 s``).
    """
    return f"{value:.3f} s"


def render_markdown_table(
    report: BenchmarkReport,
    *,
    baseline_tool: str = "lintro",
) -> str:
    """Render a benchmark report as a markdown comparison table.

    Rows are grouped by fixture and scenario. A ``relative`` column expresses
    each tool's median as a multiple of the baseline tool's median for the same
    fixture/scenario, so a value of ``2.00x`` means "twice as slow as lintro".

    Args:
        report: The report to render.
        baseline_tool: Tool used as the ``1.00x`` reference.

    Returns:
        str: A markdown table (no surrounding prose).
    """
    header = (
        "| Fixture | Scenario | Tool | Median | Min | Max | Runs | Relative |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    if not report.results:
        return header + "\n| _(no results)_ | | | | | | | |"

    # Median lookup for the baseline, keyed by (fixture, scenario).
    baseline: dict[tuple[str, str], float] = {}
    for result in report.results:
        if result.tool == baseline_tool:
            baseline[(result.fixture, result.scenario)] = result.stats.median_s

    def sort_key(result: ScenarioResult) -> tuple[str, str, float]:
        return (result.fixture, result.scenario, result.stats.median_s)

    lines = [header]
    for result in sorted(report.results, key=sort_key):
        base = baseline.get((result.fixture, result.scenario))
        if base is None or base == 0:
            relative = "n/a"
        else:
            relative = f"{result.stats.median_s / base:.2f}x"
        median = _format_seconds(result.stats.median_s)
        min_s = _format_seconds(result.stats.min_s)
        max_s = _format_seconds(result.stats.max_s)
        lines.append(
            f"| {result.fixture} | {result.scenario} | {result.label} "
            f"| {median} | {min_s} | {max_s} "
            f"| {result.stats.runs} | {relative} |",
        )
    return "\n".join(lines)
