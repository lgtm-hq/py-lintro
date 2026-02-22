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

# =============================================================================
# Console Output Method Tests
# =============================================================================


def test_console_output_no_color(logger: ThreadSafeConsoleLogger) -> None:
    """Verify console_output calls click.echo with plain text when no color specified.

    Without a color argument, the text should be passed directly to click.echo
    without any styling applied.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with patch("click.echo") as mock_echo:
        logger.console_output("test message")
        mock_echo.assert_called_once_with("test message")


def test_console_output_with_color(logger: ThreadSafeConsoleLogger) -> None:
    """Verify console_output applies color styling when color argument provided.

    When a color is specified, click.style should be called to wrap the text
    with the appropriate color, then click.echo displays the styled result.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with (
        patch("click.echo") as mock_echo,
        patch("click.style", return_value="styled text") as mock_style,
    ):
        logger.console_output("test message", color="red")
        mock_style.assert_called_once_with("test message", fg="red")
        mock_echo.assert_called_once_with("styled text")


@pytest.mark.parametrize(
    ("color", "expected_fg"),
    [
        pytest.param("red", "red", id="red"),
        pytest.param("green", "green", id="green"),
        pytest.param("yellow", "yellow", id="yellow"),
        pytest.param("cyan", "cyan", id="cyan"),
        pytest.param("blue", "blue", id="blue"),
        pytest.param("magenta", "magenta", id="magenta"),
    ],
)
def test_console_output_various_colors(
    logger: ThreadSafeConsoleLogger,
    color: str,
    expected_fg: str,
) -> None:
    """Verify console_output correctly applies various color options.

    Different color values should be passed through to click.style's fg parameter
    without modification.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        color: The color to apply to output.
        expected_fg: The expected foreground color value.
    """
    with patch("click.echo"), patch("click.style") as mock_style:
        logger.console_output("test", color=color)
        mock_style.assert_called_once_with("test", fg=expected_fg)


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


def test_save_console_log_handles_os_error(tmp_path: Path) -> None:
    """Verify save_console_log handles OSError gracefully with error log.

    When file creation fails due to OS-level issues, the error should be
    caught and logged rather than propagating as an exception.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    logger = ThreadSafeConsoleLogger(run_dir=tmp_path)
    logger._messages = ["Test message"]
    with (
        patch("builtins.open", side_effect=OSError("Permission denied")),
        patch("lintro.utils.console.logger.logger.error") as mock_error,
    ):
        logger.save_console_log()
        mock_error.assert_called_once()
        error_message = str(mock_error.call_args)
        assert_that(error_message).contains(
            "Failed to save console log",
        )


def test_save_console_log_handles_permission_error(tmp_path: Path) -> None:
    """Verify save_console_log handles PermissionError gracefully.

    Permission errors during file creation should be caught and logged
    without crashing the application.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    logger = ThreadSafeConsoleLogger(run_dir=tmp_path)
    logger._messages = ["Test message"]
    with (
        patch(
            "builtins.open",
            side_effect=PermissionError("Access denied"),
        ),
        patch("lintro.utils.console.logger.logger.error") as mock_error,
    ):
        logger.save_console_log()
        mock_error.assert_called_once()
