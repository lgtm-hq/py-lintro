"""Unit tests for ThreadSafeConsoleLogger logging level methods.

Tests cover info, debug, warning, error, and success logging methods
and verify they use appropriate colors and formatting.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.utils.console.logger import ThreadSafeConsoleLogger


def test_info_delegates_to_console_output(logger: ThreadSafeConsoleLogger) -> None:
    """Verify info() delegates to console_output without color styling.

    The info method is a convenience wrapper that passes messages directly
    to console_output without any color formatting.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with patch.object(logger, "console_output") as mock_output:
        logger.info("info message")
        mock_output.assert_called_once_with("info message")


def test_debug_uses_loguru_logger(logger: ThreadSafeConsoleLogger) -> None:
    """Verify debug() uses loguru's debug level instead of console output.

    Debug messages go to the loguru logger for proper debug-level filtering,
    rather than always appearing on the console.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with patch("lintro.utils.console.logger.logger.debug") as mock_debug:
        logger.debug("debug message")
        mock_debug.assert_called_once_with("debug message")


def test_warning_outputs_yellow_text(logger: ThreadSafeConsoleLogger) -> None:
    """Verify warning() outputs messages in yellow color with WARNING prefix.

    Warning messages should be visually distinct using yellow coloring
    and a WARNING prefix to indicate potential issues.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with patch.object(logger, "console_output") as mock_output:
        logger.warning("warning message")
        mock_output.assert_called_once_with("WARNING: warning message", color="yellow")


def test_error_outputs_red_text(logger: ThreadSafeConsoleLogger) -> None:
    """Verify error() outputs messages in red color with ERROR prefix.

    Error messages use red coloring to clearly indicate problems
    that need immediate attention.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with (
        patch("lintro.utils.console.logger.click.echo") as mock_echo,
        patch("lintro.utils.console.logger.click.style") as mock_style,
        patch("lintro.utils.console.logger.logger"),
    ):
        mock_style.return_value = "styled"
        logger.error("error message")
        mock_style.assert_called_once_with("ERROR: error message", fg="red", bold=True)
        mock_echo.assert_called_once_with("styled", err=False)
        assert_that(logger._messages).contains("ERROR: error message")


def test_success_outputs_green_text_with_checkmark(
    logger: ThreadSafeConsoleLogger,
) -> None:
    """Verify success() outputs messages in green with checkmark emoji prefix.

    Success messages include a checkmark emoji and use green coloring
    to clearly indicate successful completion of operations.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
    """
    with patch.object(logger, "console_output") as mock_output:
        logger.success("success message")
        mock_output.assert_called_once_with(
            text="\u2705 success message",
            color="green",
        )


@pytest.mark.parametrize(
    ("method", "expected_color"),
    [
        pytest.param("warning", "yellow", id="warning-yellow"),
        pytest.param("success", "green", id="success-green"),
    ],
)
def test_logging_methods_use_correct_colors(
    logger: ThreadSafeConsoleLogger,
    method: str,
    expected_color: str,
) -> None:
    """Verify each logging level method uses its designated color.

    Each logging method has an associated color to provide visual distinction
    between message severity levels in console output.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        method: The logging method name to test.
        expected_color: The expected color for the logging method.
    """
    with patch.object(logger, "console_output") as mock_output:
        getattr(logger, method)("test message")
        call_kwargs = mock_output.call_args
        assert_that(
            call_kwargs.kwargs.get(
                "color",
                call_kwargs.args[1] if len(call_kwargs.args) > 1 else None,
            ),
        ).is_equal_to(expected_color)
