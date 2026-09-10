"""Unit tests for ThreadSafeConsoleLogger logging level methods.

Tests cover info, debug, warning, error, and success logging methods
and verify they use appropriate colors and formatting.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.utils.console.logger import ThreadSafeConsoleLogger
from tests.unit.utils.console.conftest import patch_tty_streams


def test_info_prints_the_message_unstyled(
    logger: ThreadSafeConsoleLogger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An info message reaches the terminal and the buffer without colour.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)

    logger.info("info message")

    assert_that(stdout.getvalue()).is_equal_to("info message\n")
    assert_that(logger.get_buffer()).is_equal_to("info message")


def test_debug_stays_off_the_console(
    logger: ThreadSafeConsoleLogger,
    loguru_messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Debug messages go to loguru only, never to the console or the buffer.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        loguru_messages: Messages captured from the loguru sink.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)

    logger.debug("debug message")

    assert_that(loguru_messages).contains("debug message")
    assert_that(stdout.getvalue()).is_empty()
    assert_that(logger.get_buffer()).is_empty()


def test_warning_outputs_yellow_text(
    logger: ThreadSafeConsoleLogger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warning is prefixed and coloured yellow on the terminal.

    The buffer that backs ``console.log`` keeps the plain text, so the colour
    codes never reach the saved artifact.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)

    logger.warning("warning message")

    assert_that(stdout.getvalue()).is_equal_to(
        "\x1b[33mWARNING: warning message\x1b[0m\n",
    )
    assert_that(logger.get_buffer()).is_equal_to("WARNING: warning message")


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A success message is prefixed with a checkmark and coloured green.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)

    logger.success("success message")

    assert_that(stdout.getvalue()).is_equal_to(
        "\x1b[32m\u2705 success message\x1b[0m\n",
    )
    assert_that(logger.get_buffer()).is_equal_to("\u2705 success message")


@pytest.mark.parametrize(
    ("method", "expected_ansi"),
    [
        pytest.param("warning", "\x1b[33m", id="warning-yellow"),
        pytest.param("success", "\x1b[32m", id="success-green"),
        pytest.param("error", "\x1b[31m", id="error-red"),
    ],
)
def test_logging_methods_use_correct_colors(
    logger: ThreadSafeConsoleLogger,
    method: str,
    expected_ansi: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each logging level emits its own colour code on an interactive terminal.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        method: The logging method name to test.
        expected_ansi: The ANSI colour prefix the method must emit.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)

    getattr(logger, method)("test message")

    assert_that(stdout.getvalue()).starts_with(expected_ansi)
