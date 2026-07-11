"""Integration tests for DjlintPlugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if djlint is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("djlint") is None,
    reason="djlint not installed",
)


@pytest.mark.parametrize(
    ("attr", "expected"),
    [("name", "djlint"), ("can_fix", True)],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify the djlint definition exposes the expected attribute values.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check.
        expected: The expected attribute value.
    """
    plugin = get_plugin("djlint")
    assert_that(getattr(plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify the djlint definition includes template file patterns.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("djlint")
    assert_that(plugin.definition.file_patterns).contains("*.jinja", "*.j2")


def test_check_file_with_violations(
    get_plugin: Callable[[str], BaseToolPlugin],
    djlint_violation_file: str,
) -> None:
    """Verify djLint check detects formatting issues in a messy template.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        djlint_violation_file: Path to a template with formatting issues.
    """
    plugin = get_plugin("djlint")
    result = plugin.check([djlint_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("djlint")
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.success).is_false()


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    djlint_clean_file: str,
) -> None:
    """Verify djLint check passes on a well-formatted template.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        djlint_clean_file: Path to a well-formatted template.
    """
    plugin = get_plugin("djlint")
    result = plugin.check([djlint_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("djlint")
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify djLint check handles empty directories gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("djlint")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("djlint")
    assert_that(result.issues_count).is_equal_to(0)


def test_set_options_profile(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify the djlint profile option can be set and retrieved.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("djlint")
    plugin.set_options(profile="django")
    assert_that(plugin.options.get("profile")).is_equal_to("django")
