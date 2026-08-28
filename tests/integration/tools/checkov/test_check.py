"""Integration tests for CheckovPlugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.parsers.checkov.checkov_issue import CheckovIssue

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if checkov is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("checkov") is None,
    reason="checkov not installed",
)


def test_check_file_with_violations(
    get_plugin: Callable[[str], BaseToolPlugin],
    checkov_violation_file: str,
) -> None:
    """Verify checkov detects misconfigurations in a bad Terraform file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        checkov_violation_file: Path to a Terraform file with violations.
    """
    plugin = get_plugin("checkov")
    result = plugin.check([checkov_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("checkov")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)

    checkov_issues = [
        issue for issue in (result.issues or []) if isinstance(issue, CheckovIssue)
    ]
    assert_that(checkov_issues).is_not_empty()
    assert_that(checkov_issues[0].check_id).starts_with("CKV")
    assert_that(checkov_issues[0].resource).is_not_empty()


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    checkov_clean_file: str,
) -> None:
    """Verify checkov reports no issues on a resource-free Terraform file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        checkov_clean_file: Path to a clean Terraform file.
    """
    plugin = get_plugin("checkov")
    result = plugin.check([checkov_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("checkov")
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify checkov handles empty directories gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("checkov")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("checkov")
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()
