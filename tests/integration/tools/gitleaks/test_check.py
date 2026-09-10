"""Integration tests for GitleaksPlugin check command."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("gitleaks", version_args=("version",))


def test_check_file_with_secrets(
    get_plugin: Callable[[str], BaseToolPlugin],
    gitleaks_violation_file: str,
) -> None:
    """Verify gitleaks check detects secrets in problematic files.

    Runs gitleaks on a file containing deliberate secrets
    and verifies that issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        gitleaks_violation_file: Path to file with secrets from test_samples.
    """
    gitleaks_plugin = get_plugin("gitleaks")
    result = gitleaks_plugin.check([gitleaks_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("gitleaks")
    # Gitleaks should detect at least one secret pattern
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    gitleaks_clean_file: str,
) -> None:
    """Verify gitleaks check passes on clean files.

    Runs gitleaks on a file without secrets and verifies no issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        gitleaks_clean_file: Path to file with no secrets from test_samples.
    """
    gitleaks_plugin = get_plugin("gitleaks")
    result = gitleaks_plugin.check([gitleaks_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("gitleaks")
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify gitleaks check handles empty directories gracefully.

    Runs gitleaks on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    gitleaks_plugin = get_plugin("gitleaks")
    result = gitleaks_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("gitleaks")
    assert_that(result.issues_count).is_equal_to(0)
