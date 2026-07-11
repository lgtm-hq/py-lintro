"""Runner and scenario definitions for the comparative benchmark.

A :class:`Runner` maps a competitor tool onto a concrete command for a given
fixture project. Commands are argv lists (never shell strings) so they are safe
to hand directly to :func:`benchmarks.harness.timing.measure_command`.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path

from benchmarks.harness.detect import (
    Availability,
    CompetitorTool,
    detect_runners,
    which,
)

# Pinned MegaLinter image used when driving MegaLinter via the docker CLI.
MEGALINTER_IMAGE = "oxsecurity/megalinter-python:v8.9.0"


class Scenario(StrEnum):
    """Benchmark scenarios that mirror the issue's proposed matrix.

    Values are lower-case identifiers used in report keys and CLI selectors.
    """

    FULL_CHECK_COLD = auto()
    FULL_CHECK_WARM = auto()


# Warmup-run counts per scenario. A cold scenario times the very first
# invocation (no warmup); a warm scenario discards one priming run first.
SCENARIO_WARMUP: dict[Scenario, int] = {
    Scenario.FULL_CHECK_COLD: 0,
    Scenario.FULL_CHECK_WARM: 1,
}


@dataclass(frozen=True, slots=True)
class Runner:
    """A single benchmarkable command for one competitor tool.

    Attributes:
        tool: The competitor tool this runner represents.
        label: Human-readable label used in report tables.
        command: Argv-style command to execute against the fixture.
        available: Availability status for the underlying tool.
    """

    tool: CompetitorTool
    label: str
    command: list[str]
    available: Availability


def _lintro_command(fixture_dir: Path) -> list[str]:
    """Build the lintro check command for a fixture.

    Restricted to the same underlying tool set the competitors run (ruff),
    so the comparison isolates orchestration overhead rather than differing
    tool sets — the apples-to-apples contract documented in the README.

    Args:
        fixture_dir: Directory of the fixture project to check.

    Returns:
        list[str]: Argv for the lintro check.
    """
    return ["uv", "run", "lintro", "chk", "--tools", "ruff", str(fixture_dir)]


def _sequential_command(fixture_dir: Path) -> list[str]:
    """Build a raw sequential native-tool command for a fixture.

    This approximates the "run each tool by hand, one after another" workflow
    that lintro replaces. It intentionally uses the same underlying tools lintro
    orchestrates so the comparison isolates orchestration overhead rather than
    tool selection. Both commands always run inside a single measured
    ``bash -c`` process (a human runs each tool regardless of the previous
    one's findings), and the worst exit code is propagated so a failing
    ``ruff check`` is never masked by a passing format check.

    Args:
        fixture_dir: Directory of the fixture project to check.

    Returns:
        list[str]: Argv for the sequential native invocation.
    """
    target = shlex.quote(str(fixture_dir))
    sequence = (
        f"uv run ruff check {target}; c1=$?; "
        f"uv run ruff format --check {target}; c2=$?; "
        "exit $(( c1 > c2 ? c1 : c2 ))"
    )
    return ["bash", "-c", sequence]


def _pre_commit_command(fixture_dir: Path, config: Path) -> list[str]:
    """Build the pre-commit command for a fixture.

    Args:
        fixture_dir: Directory of the fixture project to check.
        config: Path to the pinned ``.pre-commit-config.yaml``.

    Returns:
        list[str]: Argv for the pre-commit run.
    """
    return [
        "pre-commit",
        "run",
        "--config",
        str(config),
        "--files",
        *(str(path) for path in sorted(fixture_dir.rglob("*.py"))),
    ]


def _megalinter_command(fixture_dir: Path, config: Path) -> list[str]:
    """Build the MegaLinter command for a fixture.

    Prefers the ``mega-linter-runner`` npm wrapper when present; otherwise
    drives the pinned MegaLinter Docker image directly via the ``docker`` CLI so
    the harness still works in environments without the npm wrapper installed.

    Args:
        fixture_dir: Directory of the fixture project to check.
        config: Path to the pinned ``.mega-linter.yml``.

    Returns:
        list[str]: Argv for the MegaLinter run.
    """
    if which("mega-linter-runner") is not None:
        return [
            "mega-linter-runner",
            "--flavor",
            "python",
            "--path",
            str(fixture_dir),
            "--filesonly",
            "--config",
            str(config),
        ]
    workspace = str(fixture_dir.resolve())
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{workspace}:/tmp/lint:ro",
        "-v",
        f"{config.resolve()}:/tmp/lint/.mega-linter.yml:ro",
        "-e",
        "DEFAULT_WORKSPACE=/tmp/lint",
        MEGALINTER_IMAGE,
    ]


def build_runners(
    fixture_dir: Path,
    *,
    config_dir: Path,
    include: Sequence[CompetitorTool] | None = None,
) -> list[Runner]:
    """Assemble the runners applicable to a fixture and environment.

    Unavailable competitors are skipped so the harness degrades to whatever can
    actually run. Lintro is always included. Passing ``include`` restricts the
    set to the requested tools (still filtered by availability).

    Args:
        fixture_dir: Directory of the fixture project to benchmark.
        config_dir: Directory holding pinned competitor config files.
        include: Optional explicit subset of competitors to consider.

    Returns:
        list[Runner]: Runners that are available and requested.
    """
    availability = detect_runners()
    requested = set(include) if include is not None else set(CompetitorTool)
    # Lintro is the baseline and is always benchmarked.
    requested.add(CompetitorTool.LINTRO)

    specs: list[tuple[CompetitorTool, str, list[str]]] = [
        (CompetitorTool.LINTRO, "lintro", _lintro_command(fixture_dir)),
        (
            CompetitorTool.SEQUENTIAL,
            "sequential-native",
            _sequential_command(fixture_dir),
        ),
        (
            CompetitorTool.PRE_COMMIT,
            "pre-commit",
            _pre_commit_command(fixture_dir, config_dir / ".pre-commit-config.yaml"),
        ),
        (
            CompetitorTool.MEGALINTER,
            "megalinter",
            _megalinter_command(fixture_dir, config_dir / ".mega-linter.yml"),
        ),
    ]

    runners: list[Runner] = []
    for tool, label, command in specs:
        if tool not in requested:
            continue
        status = availability[tool]
        if not status.available:
            continue
        runners.append(
            Runner(tool=tool, label=label, command=command, available=status),
        )
    return runners
