"""Unit tests for ThreadSafeConsoleLogger console output and log file methods.

Tests cover the console_output method with various color options and
the save_console_log file creation functionality.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.utils.console.logger import ThreadSafeConsoleLogger
from tests.unit.utils.console.conftest import patch_tty_streams

# =============================================================================
# Console Output Method Tests
# =============================================================================


def test_console_output_no_color(
    logger: ThreadSafeConsoleLogger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain text reaches stdout unstyled and is tracked in the buffer.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, stderr = patch_tty_streams(monkeypatch=monkeypatch)

    logger.console_output("test message")

    assert_that(stdout.getvalue()).is_equal_to("test message\n")
    assert_that(stderr.getvalue()).is_empty()
    assert_that(logger.get_buffer()).is_equal_to("test message")


def test_console_output_with_color(
    logger: ThreadSafeConsoleLogger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A colour argument wraps the terminal text but not the tracked buffer.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)

    logger.console_output("test message", color="red")

    assert_that(stdout.getvalue()).is_equal_to("\x1b[31mtest message\x1b[0m\n")
    assert_that(logger.get_buffer()).is_equal_to("test message")


def test_console_output_routes_to_stderr_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routed output leaves stdout empty so it stays a parseable document.

    Regression test for #1045: machine-readable formats route decorative
    output to stderr.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, stderr = patch_tty_streams(monkeypatch=monkeypatch)
    stderr_logger = ThreadSafeConsoleLogger(route_stderr=True)

    stderr_logger.console_output("banner text")

    assert_that(stderr.getvalue()).is_equal_to("banner text\n")
    assert_that(stdout.getvalue()).is_empty()


@pytest.mark.parametrize(
    ("color", "expected_ansi"),
    [
        pytest.param("red", "\x1b[31m", id="red"),
        pytest.param("green", "\x1b[32m", id="green"),
        pytest.param("yellow", "\x1b[33m", id="yellow"),
        pytest.param("blue", "\x1b[34m", id="blue"),
        pytest.param("magenta", "\x1b[35m", id="magenta"),
        pytest.param("cyan", "\x1b[36m", id="cyan"),
    ],
)
def test_console_output_various_colors(
    logger: ThreadSafeConsoleLogger,
    color: str,
    expected_ansi: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every supported colour name emits its own ANSI sequence.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        color: The color to apply to output.
        expected_ansi: The ANSI sequence the colour must produce.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)

    logger.console_output("test", color=color)

    assert_that(stdout.getvalue()).is_equal_to(f"{expected_ansi}test\x1b[0m\n")


# =============================================================================
# Console Log File Tests
# =============================================================================


def test_save_console_log_creates_file(tmp_path: Path) -> None:
    """Verify save_console_log creates console.log file in run directory.

    When a run_dir is configured, save_console_log should create a console.log
    file marker in that directory.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    logger = ThreadSafeConsoleLogger(run_dir=tmp_path)
    logger.save_console_log()
    log_file = tmp_path / "console.log"
    assert_that(log_file.exists()).is_true()


def test_save_console_log_no_run_dir_is_noop(logger: ThreadSafeConsoleLogger) -> None:
    """Verify save_console_log does nothing when no run directory configured.

    Without a run_dir, there's nowhere to save the log file, so the method
    should complete without error and without side effects.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    # Should not raise any exception
    logger.save_console_log()


def test_save_console_log_handles_os_error(
    tmp_path: Path,
    loguru_messages: list[str],
) -> None:
    """An OS-level write failure is reported and no file is written.

    The error must be caught and reported rather than propagating out of
    ``save_console_log``.

    Args:
        tmp_path: Temporary directory path for test files.
        loguru_messages: Messages captured from the loguru sink.
    """
    logger = ThreadSafeConsoleLogger(run_dir=tmp_path)
    logger._messages = ["Test message"]

    with patch("builtins.open", side_effect=OSError("Disk failure")):
        logger.save_console_log()

    reported = [
        message
        for message in loguru_messages
        if "Failed to save console log" in message
    ]
    assert_that(reported).is_length(1)
    assert_that((tmp_path / "console.log").exists()).is_false()


def test_save_console_log_handles_permission_error(
    tmp_path: Path,
    loguru_messages: list[str],
) -> None:
    """A permission error is reported and no file is written, without crashing.

    Args:
        tmp_path: Temporary directory path for test files.
        loguru_messages: Messages captured from the loguru sink.
    """
    logger = ThreadSafeConsoleLogger(run_dir=tmp_path)
    logger._messages = ["Test message"]

    with patch("builtins.open", side_effect=PermissionError("Access denied")):
        logger.save_console_log()

    assert_that(
        [message for message in loguru_messages if "Failed to save" in message],
    ).is_length(1)
    assert_that((tmp_path / "console.log").exists()).is_false()
