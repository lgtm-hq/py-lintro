"""Integration tests for PhpstanPlugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.phpstan.phpstan_issue import PhpstanIssue

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# PHPStan ships as a PHAR, so both the wrapper and a PHP runtime must exist.
pytestmark = [
    pytest.mark.phpstan,
    pytest.mark.skipif(
        shutil.which("phpstan") is None or shutil.which("php") is None,
        reason="phpstan or php not installed",
    ),
]


def test_check_file_with_violations(
    get_plugin: Callable[[str], BaseToolPlugin],
    phpstan_violation_file: str,
) -> None:
    """Verify phpstan check detects the seeded violations.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        phpstan_violation_file: Path to a PHP file with seeded violations.
    """
    plugin = get_plugin("phpstan")
    result = plugin.check([phpstan_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("phpstan")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than_or_equal_to(2)
    assert_that(result.issues).is_not_none()
    identifiers = {
        cast(PhpstanIssue, issue).identifier for issue in result.issues or []
    }
    assert_that(identifiers).contains("function.notFound")


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    phpstan_clean_file: str,
) -> None:
    """Verify phpstan check passes on a clean file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        phpstan_clean_file: Path to a clean PHP file.
    """
    plugin = get_plugin("phpstan")
    result = plugin.check([phpstan_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("phpstan")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_bare_file_does_not_crash(
    get_plugin: Callable[[str], BaseToolPlugin],
    phpstan_bare_file: str,
) -> None:
    """Analyzing a standalone file without an autoloader must not crash.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        phpstan_bare_file: Path to a standalone PHP file.
    """
    plugin = get_plugin("phpstan")
    result = plugin.check([phpstan_bare_file], {})

    assert_that(isinstance(result, ToolResult)).is_true()
    assert_that(result.name).is_equal_to("phpstan")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify phpstan check handles directories with no PHP files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("phpstan")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("phpstan")
    assert_that(result.issues_count).is_equal_to(0)
