"""Command-line entrypoint for the comparative benchmark harness.

Run with::

    uv run python -m benchmarks.run --runs 5

The harness benchmarks lintro against any competitor meta-linters available in
the current environment. When competitors are missing it benchmarks lintro
alone and records the skipped tools in the report notes.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.harness.detect import CompetitorTool, detect_runners
from benchmarks.harness.results import (
    BenchmarkReport,
    ReportMetadata,
    ScenarioResult,
    render_markdown_table,
)
from benchmarks.harness.runners import SCENARIO_WARMUP, Scenario, build_runners
from benchmarks.harness.timing import measure_command

BENCH_ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = BENCH_ROOT / "fixtures"
CONFIG_DIR = BENCH_ROOT / "configs"
RESULTS_DIR = BENCH_ROOT / "results"


def _git_sha() -> str:
    """Return the short git SHA of the current checkout.

    Returns:
        str: Short SHA, or ``"unknown"`` when git is unavailable.
    """
    try:
        completed = subprocess.run(  # noqa: S603,S607 - fixed argv, trusted
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    sha = completed.stdout.strip()
    return sha or "unknown"


def _discover_fixtures(selected: list[str] | None) -> list[Path]:
    """Discover fixture project directories to benchmark.

    Args:
        selected: Optional explicit fixture names; defaults to all directories
            under ``benchmarks/fixtures``.

    Returns:
        list[Path]: Fixture directories to benchmark.
    """
    all_fixtures = sorted(p for p in FIXTURES_DIR.iterdir() if p.is_dir())
    if not selected:
        return all_fixtures
    wanted = set(selected)
    return [p for p in all_fixtures if p.name in wanted]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        prog="benchmarks.run",
        description="Comparative benchmark harness for lintro.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Number of measured runs per scenario (default: 5).",
    )
    parser.add_argument(
        "--fixture",
        action="append",
        dest="fixtures",
        help="Fixture name to benchmark (repeatable; default: all).",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        choices=[s.value for s in Scenario],
        help="Scenario to run (repeatable; default: all).",
    )
    parser.add_argument(
        "--include",
        action="append",
        dest="include",
        choices=[t.value for t in CompetitorTool],
        help="Competitor to include (repeatable; default: all available).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=RESULTS_DIR / "latest.json",
        help="Path for the JSON report (default: benchmarks/results/latest.json).",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=RESULTS_DIR / "latest.md",
        help="Path for the markdown table (default: benchmarks/results/latest.md).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Per-run timeout in seconds (default: 600).",
    )
    return parser.parse_args(argv)


def _exit_code_notes(results: list[ScenarioResult]) -> list[str]:
    """Build notes for runs that exited non-zero.

    A non-zero exit does not necessarily invalidate a timing (linters exit
    non-zero on findings), but it must never be silent: a crashed or
    misconfigured runner would otherwise present a meaningless duration as a
    comparable result. The observed codes travel with the report so readers
    can judge comparability.

    Args:
        results: Measured scenario results.

    Returns:
        list[str]: One note per result with any non-zero exit code.
    """
    notes: list[str] = []
    for result in results:
        nonzero = [code for code in result.exit_codes if code != 0]
        if nonzero:
            notes.append(
                f"non-zero exit codes for {result.label} on "
                f"{result.fixture}/{result.scenario}: {result.exit_codes}",
            )
    return notes


def _skipped_notes() -> list[str]:
    """Build human-readable notes for skipped competitors.

    Returns:
        list[str]: One note per unavailable competitor tool.
    """
    notes: list[str] = []
    for tool, status in detect_runners().items():
        if not status.available:
            notes.append(f"skipped {tool.value}: {status.reason}")
    return notes


def main(argv: list[str] | None = None) -> int:
    """Run the comparative benchmark harness.

    Args:
        argv: Optional argument vector for testing.

    Returns:
        int: Process exit code (0 on success, 1 when no fixtures are found).
    """
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    fixtures = _discover_fixtures(args.fixtures)
    if not fixtures:
        print("No fixtures found to benchmark.", file=sys.stderr)
        return 1

    scenarios = (
        [Scenario(value) for value in args.scenarios]
        if args.scenarios
        else list(Scenario)
    )
    include = (
        [CompetitorTool(value) for value in args.include] if args.include else None
    )

    results: list[ScenarioResult] = []
    for fixture in fixtures:
        runners = build_runners(fixture, config_dir=CONFIG_DIR, include=include)
        for scenario in scenarios:
            warmup = SCENARIO_WARMUP[scenario]
            for runner in runners:
                print(
                    f"[bench] {fixture.name} / {scenario.value} / {runner.label} "
                    f"({args.runs} runs, {warmup} warmup)",
                    file=sys.stderr,
                )
                measurement = measure_command(
                    runner.command,
                    runs=args.runs,
                    warmup=warmup,
                    timeout_s=args.timeout,
                )
                results.append(
                    ScenarioResult(
                        tool=runner.tool.value,
                        label=runner.label,
                        fixture=fixture.name,
                        scenario=scenario.value,
                        stats=measurement.stats,
                        exit_codes=measurement.exit_codes,
                        command=runner.command,
                    ),
                )

    metadata = ReportMetadata(
        generated_at=datetime.now(UTC).isoformat(),
        git_sha=_git_sha(),
        platform=platform.platform(),
        python_version=platform.python_version(),
        cpu_count=os.cpu_count() or 0,
        runs=args.runs,
        notes=_skipped_notes() + _exit_code_notes(results),
    )
    report = BenchmarkReport(metadata=metadata, results=results)

    report.write_json(args.output)
    markdown = render_markdown_table(report)
    exit_notes = _exit_code_notes(results)
    if metadata.notes:
        # Surface skips and non-zero exits in the rendered report itself —
        # a reader of latest.md must not mistake a crashed runner's timing
        # for a comparable result.
        notes_md = "\n".join(f"- {note}" for note in metadata.notes)
        markdown = f"{markdown}\n\n**Notes:**\n\n{notes_md}"
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown + "\n", encoding="utf-8")

    print(markdown)
    for note in exit_notes:
        print(f"[bench] warning: {note}", file=sys.stderr)
    print(f"\nJSON report:     {args.output}", file=sys.stderr)
    print(f"Markdown report: {args.markdown}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
