"""Unit tests for ThreadSafeConsoleLogger class message tracking."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.utils.console import ThreadSafeConsoleLogger


@pytest.fixture
def logger() -> ThreadSafeConsoleLogger:
    """Create a ThreadSafeConsoleLogger instance for testing.

    Returns:
        ThreadSafeConsoleLogger instance for testing.
    """
    return ThreadSafeConsoleLogger()


def test_initialization(logger: ThreadSafeConsoleLogger) -> None:
    """Test that logger initializes with empty messages list.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    assert_that(logger._messages).is_empty()


def test_console_output_no_color(logger: ThreadSafeConsoleLogger) -> None:
    """Test console_output without color.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    with patch("lintro.utils.console.logger.click.echo") as mock_echo:
        logger.console_output("Test message")

        mock_echo.assert_called_once_with("Test message", err=False)
        assert_that(logger._messages).contains("Test message")


def test_console_output_with_color(logger: ThreadSafeConsoleLogger) -> None:
    """Test console_output with color.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    with (
        patch("lintro.utils.console.logger.click.echo") as mock_echo,
        patch("lintro.utils.console.logger.click.style") as mock_style,
    ):
        mock_style.return_value = "styled text"
        logger.console_output("Test message", color="green")

        mock_style.assert_called_once_with("Test message", fg="green")
        mock_echo.assert_called_once_with("styled text", err=False)
        assert_that(logger._messages).contains("Test message")


def test_info(logger: ThreadSafeConsoleLogger) -> None:
    """Test info method logs message.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    with (
        patch("lintro.utils.console.logger.click.echo") as mock_echo,
        patch("lintro.utils.console.logger.logger") as mock_logger,
    ):
        logger.info("Info message")

        mock_echo.assert_called_once_with("Info message", err=False)
        mock_logger.info.assert_called_once()
        assert_that(logger._messages).contains("Info message")


def test_info_blue(logger: ThreadSafeConsoleLogger) -> None:
    """Test info_blue method logs in cyan.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    with (
        patch("lintro.utils.console.logger.click.echo") as mock_echo,
        patch("lintro.utils.console.logger.click.style") as mock_style,
        patch("lintro.utils.console.logger.logger") as mock_logger,
    ):
        mock_style.return_value = "styled"
        logger.info_blue("Blue message")

        mock_style.assert_called_once_with("Blue message", fg="cyan")
        mock_echo.assert_called_once_with("styled", err=False)
        mock_logger.info.assert_called_once()
        assert_that(logger._messages).contains("Blue message")


def test_success(logger: ThreadSafeConsoleLogger) -> None:
    """Test success method logs in green with emoji.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    with (
        patch("lintro.utils.console.logger.click.echo"),
        patch("lintro.utils.console.logger.click.style") as mock_style,
        patch("lintro.utils.console.logger.logger") as mock_logger,
    ):
        mock_style.return_value = "styled"
        logger.success("Success message")

        mock_style.assert_called_once_with("✅ Success message", fg="green")
        mock_logger.info.assert_called_once()
        assert_that(logger._messages).contains("✅ Success message")


def test_warning(logger: ThreadSafeConsoleLogger) -> None:
    """Test warning method prefixes with WARNING.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    with (
        patch("lintro.utils.console.logger.click.echo"),
        patch("lintro.utils.console.logger.click.style") as mock_style,
        patch("lintro.utils.console.logger.logger") as mock_logger,
    ):
        mock_style.return_value = "styled"
        logger.warning("Warning message")

        mock_style.assert_called_once_with("WARNING: Warning message", fg="yellow")
        mock_logger.warning.assert_called_once()
        assert_that(logger._messages).contains("WARNING: Warning message")


def test_error(logger: ThreadSafeConsoleLogger) -> None:
    """Test error method prefixes with ERROR.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    with (
        patch("lintro.utils.console.logger.click.echo"),
        patch("lintro.utils.console.logger.click.style") as mock_style,
        patch("lintro.utils.console.logger.logger") as mock_logger,
    ):
        mock_style.return_value = "styled"
        logger.error("Error message")

        mock_style.assert_called_once_with("ERROR: Error message", fg="red", bold=True)
        mock_logger.error.assert_called_once()
        assert_that(logger._messages).contains("ERROR: Error message")


def test_save_console_log(logger: ThreadSafeConsoleLogger, tmp_path: Path) -> None:
    """Test saving console messages to file.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
        tmp_path: Temporary directory path for testing.
    """
    logger._messages = ["Message 1", "Message 2", "Message 3"]

    with patch("lintro.utils.console.logger.logger"):
        logger.save_console_log(tmp_path)

    log_path = tmp_path / "console.log"
    assert_that(log_path.exists()).is_true()
    content = log_path.read_text()
    assert_that(content).contains("Message 1")
    assert_that(content).contains("Message 2")
    assert_that(content).contains("Message 3")


def test_save_console_log_handles_error(logger: ThreadSafeConsoleLogger) -> None:
    """Test save_console_log handles write errors gracefully.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    logger._messages = ["Message"]

    with (
        patch("builtins.open", side_effect=OSError("Permission denied")),
        patch("lintro.utils.console.logger.logger") as mock_logger,
    ):
        logger.save_console_log("/invalid/path")
        mock_logger.error.assert_called_once()


def test_multiple_messages_tracked(logger: ThreadSafeConsoleLogger) -> None:
    """Test that multiple messages are tracked in order.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    with (
        patch("lintro.utils.console.logger.click.echo"),
        patch("lintro.utils.console.logger.click.style", return_value="s"),
        patch("lintro.utils.console.logger.logger"),
    ):
        logger.info("First")
        logger.success("Second")
        logger.warning("Third")
        logger.error("Fourth")

        assert_that(len(logger._messages)).is_equal_to(4)
        assert_that(logger._messages[0]).is_equal_to("First")
        assert_that(logger._messages[1]).is_equal_to("✅ Second")
        assert_that(logger._messages[2]).is_equal_to("WARNING: Third")
        assert_that(logger._messages[3]).is_equal_to("ERROR: Fourth")


def test_thread_safe_message_tracking(logger: ThreadSafeConsoleLogger) -> None:
    """Test that message tracking is thread-safe.

    Args:
        logger: ThreadSafeConsoleLogger instance for testing.
    """
    import threading

    messages_to_add = 100
    threads = []

    with (
        patch("lintro.utils.console.logger.click.echo"),
        patch("lintro.utils.console.logger.click.style", return_value="s"),
    ):

        def add_message(i: int) -> None:
            logger.console_output(f"Message {i}")

        for i in range(messages_to_add):
            t = threading.Thread(target=add_message, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    # All messages should be tracked without loss
    assert_that(len(logger._messages)).is_equal_to(messages_to_add)
