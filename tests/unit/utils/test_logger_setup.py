"""Tests for lintro.utils.logger_setup module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.utils.logger_setup import setup_cli_logging, setup_execution_logging

# =============================================================================
# setup_cli_logging tests
# =============================================================================


@patch("loguru.logger")
def test_setup_cli_logging_removes_handlers(mock_logger: MagicMock) -> None:
    """setup_cli_logging removes existing handlers.

    Args:
        mock_logger: Mock logger object.
    """
    setup_cli_logging()

    mock_logger.remove.assert_called_once()


@patch("loguru.logger")
def test_setup_cli_logging_adds_stderr_handler(mock_logger: MagicMock) -> None:
    """setup_cli_logging adds stderr handler.

    Args:
        mock_logger: Mock logger object.
    """
    setup_cli_logging()

    mock_logger.add.assert_called_once()
    call_args = mock_logger.add.call_args
    assert_that(call_args.kwargs["level"]).is_equal_to("WARNING")
    assert_that(call_args.kwargs["colorize"]).is_true()


# =============================================================================
# setup_execution_logging tests
# =============================================================================


@patch("loguru.logger")
def test_setup_execution_logging_removes_handlers(
    mock_logger: MagicMock,
    tmp_path: Path,
) -> None:
    """setup_execution_logging removes existing handlers.

    Args:
        mock_logger: Mock logger object.
        tmp_path: Temporary directory fixture.
    """
    setup_execution_logging(run_dir=tmp_path)

    mock_logger.remove.assert_called_once()


@patch("loguru.logger")
def test_setup_execution_logging_adds_console_and_file_handlers(
    mock_logger: MagicMock,
    tmp_path: Path,
) -> None:
    """setup_execution_logging adds console and file handlers.

    Args:
        mock_logger: Mock logger object.
        tmp_path: Temporary directory fixture.
    """
    setup_execution_logging(run_dir=tmp_path)

    # Should add two handlers: console + file
    assert_that(mock_logger.add.call_count).is_equal_to(2)


@patch("loguru.logger")
def test_setup_execution_logging_debug_false_uses_warning(
    mock_logger: MagicMock,
    tmp_path: Path,
) -> None:
    """setup_execution_logging uses WARNING level when debug=False.

    Args:
        mock_logger: Mock logger object.
        tmp_path: Temporary directory fixture.
    """
    setup_execution_logging(run_dir=tmp_path, debug=False)

    # First call is console handler
    first_call = mock_logger.add.call_args_list[0]
    assert_that(first_call.kwargs["level"]).is_equal_to("WARNING")


@patch("loguru.logger")
def test_setup_execution_logging_debug_true_uses_debug(
    mock_logger: MagicMock,
    tmp_path: Path,
) -> None:
    """setup_execution_logging uses DEBUG level when debug=True.

    Args:
        mock_logger: Mock logger object.
        tmp_path: Temporary directory fixture.
    """
    setup_execution_logging(run_dir=tmp_path, debug=True)

    # First call is console handler
    first_call = mock_logger.add.call_args_list[0]
    assert_that(first_call.kwargs["level"]).is_equal_to("DEBUG")


@patch("loguru.logger")
def test_setup_execution_logging_file_handler_uses_debug(
    mock_logger: MagicMock,
    tmp_path: Path,
) -> None:
    """setup_execution_logging file handler always uses DEBUG level.

    Args:
        mock_logger: Mock logger object.
        tmp_path: Temporary directory fixture.
    """
    setup_execution_logging(run_dir=tmp_path, debug=False)

    # Second call is file handler
    second_call = mock_logger.add.call_args_list[1]
    assert_that(second_call.kwargs["level"]).is_equal_to("DEBUG")


@patch("loguru.logger")
def test_setup_execution_logging_creates_run_dir(
    mock_logger: MagicMock,
    tmp_path: Path,
) -> None:
    """setup_execution_logging creates run directory if needed.

    Args:
        mock_logger: Mock logger object.
        tmp_path: Temporary directory fixture.
    """
    run_dir = tmp_path / "logs" / "run1"
    assert_that(run_dir.exists()).is_false()

    setup_execution_logging(run_dir=run_dir)

    assert_that(run_dir.exists()).is_true()
