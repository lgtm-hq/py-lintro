"""Integration tests for J2lintPlugin check command.

These tests require j2lint to be installed and available on PATH. They verify
the plugin definition, its detection of real violations, and its option
handling against the actual binary.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if j2lint is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("j2lint") is None,
    reason="j2lint not installed",
)


def test_definition_attributes(get_plugin: Callable[[str], BaseToolPlugin]) -> None:
    """Verify the j2lint definition exposes the expected metadata.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("j2lint")
    assert_that(plugin.definition.name).is_equal_to("j2lint")
    assert_that(plugin.definition.can_fix).is_false()


def test_definition_file_patterns(get_plugin: Callable[[str], BaseToolPlugin]) -> None:
    """Verify the j2lint definition targets Jinja2 extensions.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("j2lint")
    assert_that(plugin.definition.file_patterns).contains("*.j2")


def test_check_file_with_violations(
    get_plugin: Callable[[str], BaseToolPlugin],
    j2lint_violation_file: str,
) -> None:
    """Verify j2lint detects violations in a problematic template.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        j2lint_violation_file: Path to a template with deliberate issues.
    """
    plugin = get_plugin("j2lint")
    result = plugin.check([j2lint_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("j2lint")
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.success).is_false()


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    j2lint_clean_file: str,
) -> None:
    """Verify j2lint reports no issues for a clean template.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        j2lint_clean_file: Path to a clean template.
    """
    plugin = get_plugin("j2lint")
    result = plugin.check([j2lint_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("j2lint")
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify j2lint handles a directory with no templates gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("j2lint")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("j2lint")
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


def test_ignore_option_drops_rule(
    get_plugin: Callable[[str], BaseToolPlugin],
    j2lint_violation_file: str,
) -> None:
    """Verify the ignore option removes matching rule violations.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        j2lint_violation_file: Path to a template with deliberate issues.
    """
    plugin = get_plugin("j2lint")
    baseline = plugin.check([j2lint_violation_file], {})

    plugin.reset_options()
    plugin.set_options(ignore=["S1"])
    filtered = plugin.check([j2lint_violation_file], {})

    assert_that(filtered.issues_count).is_less_than(baseline.issues_count)
    codes = {getattr(issue, "code", "") for issue in (filtered.issues or [])}
    assert_that(codes).does_not_contain("S1")


def test_warn_option_demotes_rule(
    get_plugin: Callable[[str], BaseToolPlugin],
    j2lint_violation_file: str,
) -> None:
    """Verify the warn option demotes matching rules to warnings.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        j2lint_violation_file: Path to a template with deliberate issues.
    """
    plugin = get_plugin("j2lint")
    plugin.set_options(warn=["S3", "S6", "S1"])
    result = plugin.check([j2lint_violation_file], {})

    levels = {getattr(issue, "level", "") for issue in (result.issues or [])}
    assert_that(levels).contains("warning")
    assert_that(result.success).is_true()
