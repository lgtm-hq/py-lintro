"""Integration tests for TrivyPlugin check command.

These tests need the ``trivy`` binary **and** a populated local vulnerability
database. Trivy runs with ``--skip-db-update`` (hermetic), so on a machine with
no cached DB the plugin reports a non-fatal skip instead of scanning. Tests
detect that condition via :data:`DB_MISSING_MARKER` and skip rather than fail,
keeping CI without a DB green — mirroring how pip_audit handles missing network.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import pytest
from assertpy import assert_that

from lintro.parsers.trivy import TrivyIssue

from .conftest import DB_MISSING_MARKER

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if trivy is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("trivy") is None,
    reason="trivy not installed",
)


def _skip_if_db_missing(result: ToolResult) -> None:
    """Skip the test when trivy has no local vulnerability database.

    Args:
        result: Result returned by the trivy plugin.
    """
    if result.output and DB_MISSING_MARKER in result.output:
        pytest.skip("trivy vulnerability DB not available")


def test_check_file_with_vulnerabilities(
    get_plugin: Callable[[str], BaseToolPlugin],
    trivy_violation_file: str,
) -> None:
    """Verify trivy detects vulnerabilities in known-vulnerable pins.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        trivy_violation_file: Path to the vulnerable manifest from test_samples.
    """
    plugin = get_plugin("trivy")
    result = plugin.check([trivy_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("trivy")
    _skip_if_db_missing(result)

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)

    issues = cast("list[TrivyIssue]", list(result.issues or []))
    assert_that(issues).is_not_empty()
    assert_that(issues[0]).is_instance_of(TrivyIssue)
    assert_that(issues[0].vuln_id).is_not_empty()
    assert_that(issues[0].pkg_name).is_not_empty()


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    trivy_clean_file: str,
) -> None:
    """Verify trivy passes on a manifest that declares no dependencies.

    A comment-only ``requirements.txt`` deterministically yields zero findings
    regardless of DB contents, unlike any concrete pin that could acquire a CVE.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        trivy_clean_file: Path to the clean manifest from test_samples.
    """
    plugin = get_plugin("trivy")
    result = plugin.check([trivy_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("trivy")
    _skip_if_db_missing(result)

    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


def test_check_no_paths_is_clean(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify trivy short-circuits cleanly when given no paths.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("trivy")
    result = plugin.check([], {})

    assert_that(result.name).is_equal_to("trivy")
    assert_that(result.issues_count).is_equal_to(0)
