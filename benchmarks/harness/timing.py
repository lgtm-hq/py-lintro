"""Timing primitives and summary statistics for the benchmark harness.

The functions here are deliberately free of I/O so they can be unit tested in
isolation. ``measure_command`` is the only function that shells out; the
statistics helpers operate purely on lists of durations.
"""

from __future__ import annotations

import statistics
import subprocess  # nosec B404 - subprocess measures caller-supplied argv under test; shell=False
import time
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True, slots=True)
class TimingStats:
    """Summary statistics for a set of timed runs.

    All durations are expressed in seconds.

    Attributes:
        runs: Number of measured (non-warmup) runs.
        min_s: Fastest observed run in seconds.
        max_s: Slowest observed run in seconds.
        mean_s: Arithmetic mean of the measured runs in seconds.
        median_s: Median of the measured runs in seconds.
        stddev_s: Sample standard deviation in seconds (0.0 for a single run).
        samples: The raw per-run durations in seconds.
    """

    runs: int
    min_s: float
    max_s: float
    mean_s: float
    median_s: float
    stddev_s: float
    samples: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Serialize the statistics to a JSON-compatible dictionary.

        Returns:
            dict[str, object]: Mapping of field names to values.
        """
        return asdict(self)


def summarize(durations: list[float]) -> TimingStats:
    """Compute summary statistics for a list of run durations.

    The median is the preferred central-tendency measure for benchmark timing
    because it is robust to occasional outliers (GC pauses, scheduler jitter).

    Args:
        durations: Per-run wall-clock durations in seconds. Must be non-empty.

    Returns:
        TimingStats: Aggregated statistics for the supplied durations.

    Raises:
        ValueError: If ``durations`` is empty or contains a negative value.
    """
    if not durations:
        raise ValueError("durations must contain at least one measurement")
    if any(value < 0 for value in durations):
        raise ValueError("durations must not contain negative values")

    stddev = statistics.stdev(durations) if len(durations) > 1 else 0.0
    return TimingStats(
        runs=len(durations),
        min_s=min(durations),
        max_s=max(durations),
        mean_s=statistics.fmean(durations),
        median_s=statistics.median(durations),
        stddev_s=stddev,
        samples=list(durations),
    )


@dataclass(frozen=True, slots=True)
class CommandMeasurement:
    """Result of measuring a single command across warmup and measured runs.

    Attributes:
        stats: Summary statistics over the measured runs.
        exit_codes: Exit code observed for each measured run.
        warmup_runs: Number of discarded warmup runs.
    """

    stats: TimingStats
    exit_codes: list[int]
    warmup_runs: int


def measure_command(
    command: list[str],
    *,
    runs: int,
    warmup: int = 1,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float | None = None,
) -> CommandMeasurement:
    """Measure the wall-clock time of a command over several runs.

    A cold measurement is obtained by setting ``warmup=0``; a warm measurement
    is obtained by discarding one or more warmup runs so that filesystem and
    interpreter caches are primed before timing begins.

    Args:
        command: Argv-style command to execute.
        runs: Number of measured runs. Must be at least one.
        warmup: Number of discarded warmup runs performed before timing.
        cwd: Working directory in which to execute the command.
        env: Optional environment overrides for the subprocess.
        timeout_s: Optional per-run timeout in seconds.

    Returns:
        CommandMeasurement: Timing statistics and per-run exit codes.

    Raises:
        ValueError: If ``runs`` is less than one or ``warmup`` is negative.
    """
    if runs < 1:
        raise ValueError("runs must be at least one")
    if warmup < 0:
        raise ValueError("warmup must not be negative")

    for _ in range(warmup):
        subprocess.run(  # noqa: S603 - command is caller-controlled, not shell  # nosec B603
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )

    durations: list[float] = []
    exit_codes: list[int] = []
    for _ in range(runs):
        start = time.perf_counter()
        completed = subprocess.run(  # noqa: S603 - caller-controlled argv  # nosec B603
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        durations.append(time.perf_counter() - start)
        exit_codes.append(completed.returncode)

    return CommandMeasurement(
        stats=summarize(durations),
        exit_codes=exit_codes,
        warmup_runs=warmup,
    )
