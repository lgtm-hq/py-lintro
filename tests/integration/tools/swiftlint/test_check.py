"""Integration tests for SwiftlintPlugin check command.

These tests require SwiftLint to be installed and available on PATH. They
exercise the real ``swiftlint`` binary against the repository's Swift
fixtures.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.parsers.swiftlint.swiftlint_issue import SwiftlintIssue

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if swiftlint is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("swiftlint") is None,
    reason="swiftlint not installed",
)


def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify the plugin definition exposes the expected identity.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("swiftlint")
    assert_that(plugin.definition.name).is_equal_to("swiftlint")
    assert_that(plugin.definition.can_fix).is_true()


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify the plugin targets Swift files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("swiftlint")
    assert_that(plugin.definition.file_patterns).contains("*.swift")


def test_check_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    swiftlint_violation_file: str,
) -> None:
    """Check detects violations in a problematic Swift file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swiftlint_violation_file: Path to a file with violations.
    """
    plugin = get_plugin("swiftlint")
    result = plugin.check([swiftlint_violation_file], {})

    assert_that(result.name).is_equal_to("swiftlint")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.issues).is_not_none()
    assert result.issues is not None  # narrow type for mypy
    codes = {issue.code for issue in result.issues if isinstance(issue, SwiftlintIssue)}
    assert_that(codes).contains("line_length")


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    swiftlint_clean_file: str,
) -> None:
    """Check reports no issues for a clean Swift file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swiftlint_clean_file: Path to a clean file.
    """
    plugin = get_plugin("swiftlint")
    result = plugin.check([swiftlint_clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Check handles a directory with no Swift files gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Temporary directory with no Swift files.
    """
    plugin = get_plugin("swiftlint")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_set_options_timeout(
    get_plugin: Callable[[str], BaseToolPlugin],
    swiftlint_violation_file: str,
) -> None:
    """set_options stores timeout=90 and check still reports violations.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swiftlint_violation_file: Path to a file with violations.
    """
    plugin = get_plugin("swiftlint")
    plugin.set_options(timeout=90)
    result = plugin.check([swiftlint_violation_file], {})

    assert_that(plugin.options["timeout"]).is_equal_to(90)
    assert_that(result.name).is_equal_to("swiftlint")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
