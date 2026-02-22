"""Unit tests for ThreadSafeConsoleLogger initialization.

Tests verify that the logger correctly initializes with and without
a run directory configuration.
"""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.utils.console.logger import ThreadSafeConsoleLogger


def test_init_with_run_dir(tmp_path: Path) -> None:
    """Verify ThreadSafeConsoleLogger correctly stores run directory when provided.

    The run_dir parameter enables output logging to a specific directory.
    When provided, it should be accessible via the run_dir attribute.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    logger = ThreadSafeConsoleLogger(run_dir=tmp_path)
    assert_that(logger.run_dir).is_equal_to(tmp_path)


def test_init_without_run_dir() -> None:
    """Verify ThreadSafeConsoleLogger initializes with None when no run directory.

    When no run_dir is given, the attribute should be None, which disables
    file-based console logging features.
    """
    logger = ThreadSafeConsoleLogger()
    assert_that(logger.run_dir).is_none()
