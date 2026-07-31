"""Integration tests for HtmlValidatePlugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if html-validate is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("html-validate") is None,
    reason="html-validate not installed",
)


def test_check_file_with_violations(
    get_plugin: Callable[[str], BaseToolPlugin],
    html_validate_violation_file: str,
) -> None:
    """Verify html-validate check detects markup issues in a bad file.

    Runs html-validate on a file with deliberate violations (missing alt,
    empty button, unclosed div) and verifies at least one issue is reported.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        html_validate_violation_file: Path to file with violations.
    """
    plugin = get_plugin("html_validate")
    result = plugin.check([html_validate_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("html_validate")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    html_validate_clean_file: str,
) -> None:
    """Verify html-validate check passes on a clean file.

    Runs html-validate on a well-formed HTML file and verifies no issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        html_validate_clean_file: Path to a clean HTML file.
    """
    plugin = get_plugin("html_validate")
    result = plugin.check([html_validate_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("html_validate")
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify html-validate check handles empty directories gracefully.

    Runs html-validate on an empty directory and verifies a result is
    returned without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("html_validate")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("html_validate")
    assert_that(result.issues_count).is_equal_to(0)
