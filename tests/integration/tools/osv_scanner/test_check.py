"""Integration tests for OsvScannerPlugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if osv-scanner is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("osv-scanner") is None,
    reason="osv-scanner not installed",
)


def test_check_file_with_vulnerabilities(
    get_plugin: Callable[[str], BaseToolPlugin],
    osv_violation_file: str,
) -> None:
    """Verify osv-scanner detects vulnerabilities in known-vulnerable packages.

    Uses the osv_scanner_violations.txt fixture which contains packages
    with known CVEs (requests==2.25.0, flask==2.0.0, django==3.2.0).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        osv_violation_file: Path to vulnerable lockfile from test_samples.
    """
    plugin = get_plugin("osv_scanner")
    result = plugin.check([osv_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("osv_scanner")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    osv_clean_file: str,
) -> None:
    """Verify osv-scanner finds no vulnerabilities in a clean lockfile.

    Note: osv-scanner may return non-zero (exit 128) for files with no
    parseable packages, so we only assert on issues_count, not success.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        osv_clean_file: Path to clean lockfile from test_samples.
    """
    plugin = get_plugin("osv_scanner")
    result = plugin.check([osv_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("osv_scanner")
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify osv-scanner check handles empty directories gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("osv_scanner")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
