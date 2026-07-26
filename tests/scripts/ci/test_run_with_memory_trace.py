# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the memory-trace wrapper used to diagnose gate kills (#1761)."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - drives a repo shell script; shell=False
from pathlib import Path

from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "scripts" / "ci" / "run-with-memory-trace.sh"


def _run(
    *args: str,
    trace_log: Path,
    interval: str = "1",
) -> subprocess.CompletedProcess[str]:
    """Run the wrapper with a scoped trace log.

    Args:
        *args: Command and arguments to wrap.
        trace_log: Sampler log path for this invocation.
        interval: Seconds between snapshots.

    Returns:
        The completed process.
    """
    env = {
        **os.environ,
        "MEMORY_TRACE_LOG": str(trace_log),
        "MEMORY_TRACE_INTERVAL": interval,
    }
    return subprocess.run(  # nosec B603 - fixed argv, shell=False
        [str(WRAPPER), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def test_wrapper_preserves_a_successful_exit_code(tmp_path: Path) -> None:
    """A passing command still passes when wrapped.

    Instrumentation must not change whether the gate passes, or it would be
    worse than the problem it is diagnosing.

    Args:
        tmp_path: Temporary directory for the trace log.
    """
    result = _run("true", trace_log=tmp_path / "trace.log")

    assert_that(result.returncode).is_equal_to(0)


def test_wrapper_preserves_a_failing_exit_code(tmp_path: Path) -> None:
    """A failing command's exact exit code is propagated.

    The gate distinguishes exit codes (1 is a lint verdict, 143 is SIGTERM),
    so collapsing them would corrupt the code-quality gate's classification.

    Args:
        tmp_path: Temporary directory for the trace log.
    """
    result = _run("bash", "-c", "exit 7", trace_log=tmp_path / "trace.log")

    assert_that(result.returncode).is_equal_to(7)


def test_wrapper_streams_samples_to_stdout(tmp_path: Path) -> None:
    """Samples appear on stdout, not only in the log file.

    A runner shutdown skips later steps, so an artifact upload would never
    run. Streaming into the step log is what makes the evidence survive.

    Args:
        tmp_path: Temporary directory for the trace log.
    """
    result = _run(
        "bash",
        "-c",
        "sleep 3",
        trace_log=tmp_path / "trace.log",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("[mem]")
    assert_that(result.stdout).contains("snapshot")


def test_wrapper_passes_command_output_through(tmp_path: Path) -> None:
    """The wrapped command's own output is not swallowed.

    Args:
        tmp_path: Temporary directory for the trace log.
    """
    result = _run(
        "bash",
        "-c",
        "echo GATE_OUTPUT_MARKER",
        trace_log=tmp_path / "trace.log",
    )

    assert_that(result.stdout).contains("GATE_OUTPUT_MARKER")


def test_wrapper_requires_a_command(tmp_path: Path) -> None:
    """Invoking with no command is a usage error, not a silent success.

    Args:
        tmp_path: Temporary directory for the trace log.
    """
    result = _run(trace_log=tmp_path / "trace.log")

    assert_that(result.returncode).is_equal_to(2)


def test_wrapper_exposes_help() -> None:
    """``--help`` documents the wrapper without running anything."""
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [str(WRAPPER), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("memory trace")


def test_final_snapshot_reaches_stdout(tmp_path: Path) -> None:
    """The sampler's last snapshot must be streamed, not just written to file.

    Cleanup originally stopped the streamer before the sampler, so the final
    snapshot — the measurement closest to a kill, and the point of the trace —
    landed only in a local file that a dying runner never uploads (#1761
    review).

    Args:
        tmp_path: Temporary directory for the trace log.
    """
    result = _run(
        "bash",
        "-c",
        "sleep 3",
        trace_log=tmp_path / "trace.log",
        interval="2",
    )

    assert_that(result.returncode).is_equal_to(0)
    # The stop marker is appended after the final snapshot, so seeing it on
    # stdout proves the tail of the trace was flushed rather than discarded.
    assert_that(result.stdout).contains("sampler stopped")


def test_each_trace_line_is_streamed_exactly_once(tmp_path: Path) -> None:
    """Streamed samples must not be replayed by the cleanup flush.

    The streamer runs in a background subshell, so a cursor kept in a shell
    variable stayed at zero in the parent and the cleanup flush re-emitted the
    whole trace — duplicating every ``[mem]`` line already in the job log
    (#1761 review). The cursor is shared through a file instead.

    Args:
        tmp_path: Temporary directory for the trace log.
    """
    trace_log = tmp_path / "trace.log"
    result = _run(
        "bash",
        "-c",
        "sleep 5",
        trace_log=trace_log,
        interval="1",
    )

    assert_that(result.returncode).is_equal_to(0)
    trace_lines = trace_log.read_text().splitlines()
    assert_that(trace_lines).is_not_empty()

    streamed = [
        line.removeprefix("[mem] ")
        for line in result.stdout.splitlines()
        if line.startswith("[mem] ")
    ]
    assert_that(streamed).is_equal_to(trace_lines)


def test_wrapper_terminates_when_no_samples_are_written(tmp_path: Path) -> None:
    """A run shorter than the sample interval still exits promptly.

    An earlier design backgrounded a pipeline and killed the wrong PID, leaving
    ``tail -F`` alive holding stdout open so the step never finished.

    Args:
        tmp_path: Temporary directory for the trace log.
    """
    result = _run(
        "true",
        trace_log=tmp_path / "trace.log",
        interval="3600",
    )

    assert_that(result.returncode).is_equal_to(0)
