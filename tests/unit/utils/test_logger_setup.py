"""Tests for lintro.utils.logger_setup module.

The setup functions reconfigure the process-wide loguru sink, so every test
here asserts on what actually reaches a sink afterwards — captured stderr or
the on-disk ``debug.log`` — rather than on how ``logger.add`` was called. The
autouse fixture puts a plain stderr sink back so the mutation stays local to
this module (#2315).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.utils.logger_setup import setup_cli_logging, setup_execution_logging

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture(autouse=True)
def restore_loguru_handlers() -> Generator[None]:
    """Give loguru a plain stderr sink back after each test.

    Both functions under test call ``logger.remove()``, which closes every
    existing handler, so the pre-test handlers cannot be handed back. Adding a
    fresh stderr sink leaves the logger in a usable default state for whatever
    runs next.

    Yields:
        None: Restores a default stderr sink once the test has finished.
    """
    try:
        yield
    finally:
        logger.remove()
        default_stderr = sys.__stderr__
        if default_stderr is not None:
            logger.add(default_stderr, level="DEBUG")


def _read_debug_log(run_dir: Path) -> str:
    """Return the contents of the execution debug log, flushing loguru first.

    Args:
        run_dir: Run directory that ``setup_execution_logging`` was given.

    Returns:
        str: The text written to ``debug.log``, or an empty string when the
        file does not exist.
    """
    logger.complete()
    debug_log = run_dir / "debug.log"
    return debug_log.read_text(encoding="utf-8") if debug_log.exists() else ""


# =============================================================================
# setup_cli_logging tests
# =============================================================================


def test_setup_cli_logging_replaces_existing_handlers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A message reaches stderr once, not once per handler added before.

    Args:
        capsys: Pytest capture fixture for stdout and stderr.
    """
    logger.add(sys.stderr, level="DEBUG", format="{message}")
    logger.add(sys.stderr, level="DEBUG", format="{message}")

    setup_cli_logging()
    logger.warning("only-once")

    assert_that(capsys.readouterr().err.count("only-once")).is_equal_to(1)


def test_setup_cli_logging_emits_warnings_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Warning-level records reach stderr after CLI logging is configured.

    Args:
        capsys: Pytest capture fixture for stdout and stderr.
    """
    setup_cli_logging()

    logger.warning("cli-warning-visible")

    captured = capsys.readouterr()
    assert_that(captured.err).contains("cli-warning-visible")
    assert_that(captured.out).does_not_contain("cli-warning-visible")


def test_setup_cli_logging_suppresses_info_and_debug(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Records below WARNING are filtered out of the CLI console output.

    Args:
        capsys: Pytest capture fixture for stdout and stderr.
    """
    setup_cli_logging()

    logger.info("cli-info-hidden")
    logger.debug("cli-debug-hidden")
    logger.error("cli-error-visible")

    captured_err = capsys.readouterr().err
    assert_that(captured_err).does_not_contain("cli-info-hidden")
    assert_that(captured_err).does_not_contain("cli-debug-hidden")
    assert_that(captured_err).contains("cli-error-visible")


# =============================================================================
# setup_execution_logging tests
# =============================================================================


def test_setup_execution_logging_replaces_existing_handlers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A warning reaches stderr once, not once per handler added before.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture for stdout and stderr.
    """
    logger.add(sys.stderr, level="DEBUG", format="{message}")
    logger.add(sys.stderr, level="DEBUG", format="{message}")

    setup_execution_logging(run_dir=tmp_path)
    logger.warning("execution-once")

    assert_that(capsys.readouterr().err.count("execution-once")).is_equal_to(1)


def test_setup_execution_logging_writes_to_console_and_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A warning lands on stderr and in the run directory's debug log.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture for stdout and stderr.
    """
    setup_execution_logging(run_dir=tmp_path)

    logger.warning("execution-warning")

    assert_that(capsys.readouterr().err).contains("execution-warning")
    assert_that(_read_debug_log(run_dir=tmp_path)).contains("execution-warning")


def test_setup_execution_logging_debug_false_hides_debug_on_console(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without ``debug``, DEBUG records reach the file but not the console.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture for stdout and stderr.
    """
    setup_execution_logging(run_dir=tmp_path, debug=False)

    logger.debug("quiet-debug-record")

    assert_that(capsys.readouterr().err).does_not_contain("quiet-debug-record")
    assert_that(_read_debug_log(run_dir=tmp_path)).contains("quiet-debug-record")


def test_setup_execution_logging_debug_true_shows_debug_on_console(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With ``debug``, DEBUG records reach the console as well as the file.

    Args:
        tmp_path: Temporary directory fixture.
        capsys: Pytest capture fixture for stdout and stderr.
    """
    setup_execution_logging(run_dir=tmp_path, debug=True)

    logger.debug("loud-debug-record")

    assert_that(capsys.readouterr().err).contains("loud-debug-record")
    assert_that(_read_debug_log(run_dir=tmp_path)).contains("loud-debug-record")


def test_setup_execution_logging_file_records_carry_source_location(
    tmp_path: Path,
) -> None:
    """The file sink uses the detailed format with level and source location.

    Args:
        tmp_path: Temporary directory fixture.
    """
    setup_execution_logging(run_dir=tmp_path)

    logger.warning("formatted-record")

    contents = _read_debug_log(run_dir=tmp_path)
    assert_that(contents).contains("WARNING")
    assert_that(contents).contains("test_setup_execution_logging_file_records")
    assert_that(contents).contains("formatted-record")


def test_setup_execution_logging_creates_run_dir(tmp_path: Path) -> None:
    """setup_execution_logging creates run directory if needed.

    Args:
        tmp_path: Temporary directory fixture.
    """
    run_dir = tmp_path / "logs" / "run1"
    assert_that(run_dir.exists()).is_false()

    setup_execution_logging(run_dir=run_dir)

    assert_that(run_dir.exists()).is_true()
