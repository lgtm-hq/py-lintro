"""Unit tests for run_subprocess_streaming function."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import run_subprocess_streaming


class _RecordingProcess:
    """Stand-in for ``subprocess.Popen`` that records how it was driven.

    Recording ``kill()`` and the wait budgets on the object itself lets a test
    assert on real process state instead of on mock call bookkeeping (#2315).

    Args:
        stdout_lines: Lines the streaming reader should see on stdout. Ignored
            when ``stdout_iter`` is given.
        stdout_iter: Ready-made stdout iterator, for readers that have to
            block rather than yield a fixed list.
        wait_error: Exception ``wait()`` should raise instead of returning.
        returncode: Exit status ``wait()`` returns when it does not raise.
    """

    def __init__(
        self,
        *,
        stdout_lines: list[str] | None = None,
        stdout_iter: Iterator[str] | None = None,
        wait_error: BaseException | None = None,
        returncode: int = 0,
    ) -> None:
        self.stdout: Iterator[str] = (
            stdout_iter if stdout_iter is not None else iter(stdout_lines or [])
        )
        self.returncode = returncode
        self.kill_count = 0
        self.wait_timeouts: list[float | None] = []
        self._wait_error = wait_error

    def wait(self, timeout: float | None = None) -> int:
        """Return the exit status, or raise the configured error.

        Args:
            timeout: Remaining budget the caller allows for the wait.

        Returns:
            int: The configured return code.

        Raises:
            self._wait_error: The configured error, when one was given.
        """
        self.wait_timeouts.append(timeout)
        if self._wait_error is not None:
            raise self._wait_error
        return self.returncode

    def kill(self) -> None:
        """Record that the process was killed."""
        self.kill_count += 1


# =============================================================================
# run_subprocess_streaming - Success Cases
# =============================================================================


def test_streaming_successful_command() -> None:
    """Verify successful streaming command returns True and captured output."""
    with patch("lintro.plugins.subprocess_executor.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdout = iter(["line1\n", "line2\n"])
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        result = run_subprocess_streaming(["echo", "hello"], timeout=30)

        assert_that(result.success).is_true()
        assert_that(result.output).contains("line1")
        assert_that(result.output).contains("line2")


def test_streaming_failed_command_nonzero_exit() -> None:
    """Verify failed streaming command returns False and logs output."""
    with patch("lintro.plugins.subprocess_executor.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdout = iter(["error output\n"])
        mock_process.wait.return_value = 1
        mock_popen.return_value = mock_process

        result = run_subprocess_streaming(["false"], timeout=30)

        assert_that(result.success).is_false()
        assert_that(result.output).contains("error output")


def test_streaming_with_line_handler() -> None:
    """Verify line handler is called for each output line."""
    lines_received: list[str] = []

    def handler(line: str) -> None:
        lines_received.append(line)

    with patch("lintro.plugins.subprocess_executor.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdout = iter(["first\n", "second\n", "third\n"])
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        run_subprocess_streaming(["echo"], timeout=30, line_handler=handler)

        assert_that(lines_received).is_length(3)
        assert_that(lines_received).contains("first")
        assert_that(lines_received).contains("second")
        assert_that(lines_received).contains("third")


# =============================================================================
# run_subprocess_streaming - Timeout Cases
# =============================================================================


def test_streaming_timeout_during_read() -> None:
    """A reader thread still alive after join raises and kills the process."""
    process = _RecordingProcess(stdout_lines=[])

    with (
        patch(
            "lintro.plugins.subprocess_executor.subprocess.Popen",
            return_value=process,
        ),
        patch("lintro.plugins.subprocess_executor.threading.Thread") as mock_thread,
    ):
        # Simulate the reader thread still running once join() returns.
        mock_thread_instance = MagicMock()
        mock_thread_instance.is_alive.return_value = True
        mock_thread.return_value = mock_thread_instance

        with pytest.raises(subprocess.TimeoutExpired):
            run_subprocess_streaming(["long", "cmd"], timeout=1)

    assert_that(process.kill_count).is_equal_to(1)


def test_streaming_wait_receives_remaining_timeout_budget() -> None:
    """process.wait() gets the remaining budget, not the full timeout.

    Regression test for issue #1047: previously the reader-thread join and
    the subsequent ``process.wait()`` each received the full timeout,
    allowing a tool to run for up to ~2x its configured limit.
    """
    read_delay = 0.4
    timeout = 2.0

    def slow_stdout() -> Iterator[str]:
        """Yield no lines but block the reader thread for ``read_delay``.

        Yields:
            str: Nothing; the generator exists only to consume wall time.
        """
        time.sleep(read_delay)
        return
        yield  # pragma: no cover - makes this a generator

    process = _RecordingProcess(stdout_iter=slow_stdout())

    with patch(
        "lintro.plugins.subprocess_executor.subprocess.Popen",
        return_value=process,
    ):
        result = run_subprocess_streaming(["slow", "cmd"], timeout=timeout)

    assert_that(result.success).is_true()
    assert_that(process.wait_timeouts).is_length(1)
    wait_timeout = process.wait_timeouts[0]
    # The wait budget must be strictly less than the full timeout since
    # the reader already consumed part of it.
    assert_that(wait_timeout).is_less_than(timeout)
    assert_that(wait_timeout).is_less_than_or_equal_to(timeout - read_delay + 0.25)
    assert_that(wait_timeout).is_greater_than_or_equal_to(0.0)


# Drives a real child process past a one-second timeout, so the wall-clock
# cost is the assertion itself and cannot be faked away (#2315).
@pytest.mark.slow
def test_streaming_total_walltime_stays_within_budget_on_hang() -> None:
    """A hanging process is bounded by the configured timeout plus epsilon.

    Runs a real child process that sleeps far beyond the timeout and produces
    no output, then asserts the total wall time stays within ``timeout`` plus
    a small epsilon rather than a multiple of it.
    """
    timeout = 1.0
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess_streaming(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=timeout,
        )
    elapsed = time.monotonic() - start
    assert_that(elapsed).is_less_than(timeout + 2.0)


def test_streaming_timeout_during_wait() -> None:
    """A timeout inside ``process.wait`` raises and kills the process."""
    process = _RecordingProcess(
        stdout_lines=["partial\n"],
        wait_error=subprocess.TimeoutExpired(cmd=["slow"], timeout=1),
    )

    with patch(
        "lintro.plugins.subprocess_executor.subprocess.Popen",
        return_value=process,
    ):
        with pytest.raises(subprocess.TimeoutExpired):
            run_subprocess_streaming(["slow", "cmd"], timeout=1)

    assert_that(process.kill_count).is_equal_to(1)


# =============================================================================
# run_subprocess_streaming - FileNotFoundError Cases
# =============================================================================


def test_streaming_file_not_found() -> None:
    """Verify FileNotFoundError is raised when command is not found."""
    with patch("lintro.plugins.subprocess_executor.subprocess.Popen") as mock_popen:
        mock_popen.side_effect = FileNotFoundError("not found")

        with pytest.raises(FileNotFoundError, match="Command not found"):
            run_subprocess_streaming(["nonexistent"], timeout=30)


# =============================================================================
# run_subprocess_streaming - Edge Cases
# =============================================================================


def test_streaming_empty_output() -> None:
    """Verify empty output is handled correctly."""
    with patch("lintro.plugins.subprocess_executor.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        result = run_subprocess_streaming(["true"], timeout=30)

        assert_that(result.success).is_true()
        assert_that(result.output).is_equal_to("")


def test_streaming_with_cwd_and_env(tmp_path: Path) -> None:
    """A real child process runs in the given cwd with the given env.

    Drives an actual interpreter that reports its own working directory and
    environment, so the assertion is on where the process really ran rather
    than on the arguments handed to ``Popen`` (#2315). ``PATH`` must survive
    because the custom env is merged into the inherited one, not substituted
    for it.

    Args:
        tmp_path: Temporary directory the child process should run in.
    """
    report = (
        "import os;"
        "print(os.path.realpath(os.getcwd()));"
        "print(os.environ['MY_VAR']);"
        "print('PATH' in os.environ)"
    )

    result = run_subprocess_streaming(
        [sys.executable, "-c", report],
        timeout=30,
        cwd=str(tmp_path),
        env={"MY_VAR": "value"},
    )

    assert_that(result.success).is_true()
    observed_cwd, observed_var, has_path = result.output.strip().splitlines()
    assert_that(Path(observed_cwd)).is_equal_to(tmp_path.resolve())
    assert_that(observed_var).is_equal_to("value")
    assert_that(has_path).is_equal_to("True")
