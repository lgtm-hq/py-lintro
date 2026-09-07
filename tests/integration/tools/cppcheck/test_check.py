"""Integration tests for CppcheckPlugin against a real cppcheck binary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.parsers.cppcheck.cppcheck_issue import CppcheckIssue
from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("cppcheck")


def test_check_detects_seeded_defects(
    get_plugin: Callable[[str], BaseToolPlugin],
    cppcheck_violation_file: str,
) -> None:
    """Cppcheck detects the seeded defects (buffer overrun, uninit var, leak).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        cppcheck_violation_file: Path to the seeded-defect fixture.
    """
    plugin = get_plugin("cppcheck")
    result = plugin.check([cppcheck_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("cppcheck")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)

    codes = {str(getattr(i, "code", "")) for i in (result.issues or [])}
    assert_that(codes).contains("arrayIndexOutOfBounds")
    assert_that(codes).contains("uninitvar")
    assert_that(codes).contains("memleak")


def test_check_clean_file_passes(
    get_plugin: Callable[[str], BaseToolPlugin],
    cppcheck_clean_file: str,
) -> None:
    """Cppcheck reports no issues on a clean file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        cppcheck_clean_file: Path to the clean fixture.
    """
    plugin = get_plugin("cppcheck")
    result = plugin.check([cppcheck_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_preserves_error_severity(
    get_plugin: Callable[[str], BaseToolPlugin],
    cppcheck_violation_file: str,
) -> None:
    """The buffer-overrun finding retains its native 'error' severity.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        cppcheck_violation_file: Path to the seeded-defect fixture.
    """
    plugin = get_plugin("cppcheck")
    result = plugin.check([cppcheck_violation_file], {})

    overruns = [
        issue
        for issue in (result.issues or [])
        if isinstance(issue, CppcheckIssue) and issue.code == "arrayIndexOutOfBounds"
    ]
    assert_that(overruns).is_not_empty()
    issue = overruns[0]
    assert_that(issue.severity).is_equal_to("error")
    assert_that(issue.cwe).is_greater_than(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Cppcheck handles a directory with no C/C++ files gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("cppcheck")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    # success matters as much as the count: a fail-closed empty argv would also
    # report zero issues, so asserting only the count would not distinguish
    # "nothing to do" from "the invocation broke".
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("No .c/.cpp")
