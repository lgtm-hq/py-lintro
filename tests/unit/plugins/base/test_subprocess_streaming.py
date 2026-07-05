"""Unit tests for run_subprocess_streaming function."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import run_subprocess_streaming

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
    """Verify TimeoutExpired is raised when reading times out."""
    with (
        patch("lintro.plugins.subprocess_executor.subprocess.Popen") as mock_popen,
        patch("lintro.plugins.subprocess_executor.threading.Thread") as mock_thread,
    ):
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_popen.return_value = mock_process

        # Simulate thread still alive after join (timeout occurred)
        mock_thread_instance = MagicMock()
        mock_thread_instance.is_alive.return_value = True
        mock_thread.return_value = mock_thread_instance

        with pytest.raises(subprocess.TimeoutExpired):
            run_subprocess_streaming(["long", "cmd"], timeout=1)

        mock_process.kill.assert_called_once()


def test_streaming_wait_receives_remaining_timeout_budget() -> None:
    """process.wait() gets the remaining budget, not the full timeout.

    Regression test for issue #1047: previously the reader-thread join and
    the subsequent ``process.wait()`` each received the full timeout,
    allowing a tool to run for up to ~2x its configured limit.
    """
    read_delay = 0.4
    timeout = 2.0

    def slow_stdout() -> Iterator[str]:
        """Yield no lines but block the reader thread for ``read_delay``."""
        time.sleep(read_delay)
        return
        yield  # pragma: no cover - makes this a generator

    with patch("lintro.plugins.subprocess_executor.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdout = slow_stdout()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        run_subprocess_streaming(["slow", "cmd"], timeout=timeout)

        wait_timeout = mock_process.wait.call_args.kwargs["timeout"]
        # The wait budget must be strictly less than the full timeout since
        # the reader already consumed part of it.
        assert_that(wait_timeout).is_less_than(timeout)
        assert_that(wait_timeout).is_less_than_or_equal_to(timeout - read_delay + 0.25)
        assert_that(wait_timeout).is_greater_than_or_equal_to(0.0)


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
    """Verify TimeoutExpired is raised when process.wait times out."""
    with patch("lintro.plugins.subprocess_executor.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdout = iter(["partial\n"])
        mock_process.wait.side_effect = subprocess.TimeoutExpired(
            cmd=["slow"],
            timeout=1,
        )
        mock_popen.return_value = mock_process

        with pytest.raises(subprocess.TimeoutExpired):
            run_subprocess_streaming(["slow", "cmd"], timeout=1)

        mock_process.kill.assert_called_once()


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


def test_streaming_with_cwd_and_env() -> None:
    """Verify cwd and env are passed to Popen."""
    with patch("lintro.plugins.subprocess_executor.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        custom_env = {"MY_VAR": "value"}
        run_subprocess_streaming(
            ["cmd"],
            timeout=30,
            cwd="/custom/path",
            env=custom_env,
        )

        mock_popen.assert_called_once()
        call_kwargs = mock_popen.call_args[1]
        assert_that(call_kwargs["cwd"]).is_equal_to("/custom/path")
        # Custom env is merged with os.environ to preserve PATH
        assert_that(call_kwargs["env"]["MY_VAR"]).is_equal_to("value")
        assert_that(call_kwargs["env"]).contains_key("PATH")
