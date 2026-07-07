"""Integration tests for CppcheckPlugin against a real cppcheck binary."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.parsers.cppcheck.cppcheck_issue import CppcheckIssue
    from lintro.plugins.base import BaseToolPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("cppcheck") is None,
    reason="cppcheck not installed",
)


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
        i
        for i in (result.issues or [])
        if str(getattr(i, "code", "")) == "arrayIndexOutOfBounds"
    ]
    assert_that(overruns).is_not_empty()
    issue: CppcheckIssue = overruns[0]  # type: ignore[assignment]
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
    assert_that(result.issues_count).is_equal_to(0)
