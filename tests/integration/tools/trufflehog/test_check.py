"""Integration tests for TrufflehogPlugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if trufflehog is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("trufflehog") is None,
    reason="trufflehog not installed",
)


def test_check_file_with_secrets(
    get_plugin: Callable[[str], BaseToolPlugin],
    trufflehog_violation_file: str,
) -> None:
    """Verify trufflehog check detects secrets in problematic files.

    Runs trufflehog on a file containing a deliberate fake credential and
    verifies that at least one issue is reported.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        trufflehog_violation_file: Path to file with secrets from test_samples.
    """
    trufflehog_plugin = get_plugin("trufflehog")
    result = trufflehog_plugin.check([trufflehog_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("trufflehog")
    # trufflehog should detect at least one secret pattern.
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    trufflehog_clean_file: str,
) -> None:
    """Verify trufflehog check passes on clean files.

    Runs trufflehog on a file without secrets and verifies no issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        trufflehog_clean_file: Path to file with no secrets from test_samples.
    """
    trufflehog_plugin = get_plugin("trufflehog")
    result = trufflehog_plugin.check([trufflehog_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("trufflehog")
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify trufflehog check handles empty directories gracefully.

    Runs trufflehog on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    trufflehog_plugin = get_plugin("trufflehog")
    result = trufflehog_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("trufflehog")
    assert_that(result.issues_count).is_equal_to(0)
