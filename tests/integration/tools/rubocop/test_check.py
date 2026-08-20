"""Integration tests for RubocopPlugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if rubocop is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("rubocop") is None,
    reason="rubocop not installed",
)


def test_check_file_with_violations(
    get_plugin: Callable[[str], BaseToolPlugin],
    rubocop_violation_file: str,
) -> None:
    """Verify rubocop check detects offenses in a bad file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        rubocop_violation_file: Path to file with violations.
    """
    plugin = get_plugin("rubocop")
    result = plugin.check([rubocop_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("rubocop")
    assert_that(result.issues_count).is_greater_than(0)
    codes = [getattr(issue, "code", "") for issue in (result.issues or [])]
    assert_that(any("/" in code for code in codes)).is_true()


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    rubocop_clean_file: str,
) -> None:
    """Verify rubocop check passes on a clean file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        rubocop_clean_file: Path to a clean Ruby file.
    """
    plugin = get_plugin("rubocop")
    result = plugin.check([rubocop_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("rubocop")
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify rubocop check handles empty directories gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("rubocop")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("rubocop")
    assert_that(result.issues_count).is_equal_to(0)
