"""Integration tests for the djLint tool definition.

These tests require djlint to be installed and available in PATH. They verify
the DjlintPlugin definition, check command, and fix command against real files.
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

pytestmark = pytest.mark.skipif(
    shutil.which("djlint") is None,
    reason="djlint not installed",
)

_MESSY_TEMPLATE = (
    "<div>\n"
    '<img src="logo.png">\n'
    "    <p>Welcome, {{ user.name }}</p>\n"
    "        <span>Poorly indented</span>\n"
    "</div>\n"
)

_CLEAN_TEMPLATE = "<div>\n    <p>Welcome, {{ user.name }}</p>\n</div>\n"


@pytest.fixture
def messy_template(tmp_path: Path) -> str:
    """Create a temporary template with formatting issues.

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        Path to the created template file as a string.
    """
    file_path = tmp_path / "messy.jinja"
    file_path.write_text(_MESSY_TEMPLATE)
    return str(file_path)


@pytest.fixture
def clean_template(tmp_path: Path) -> str:
    """Create a temporary, well-formatted template.

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        Path to the created template file as a string.
    """
    file_path = tmp_path / "clean.jinja"
    file_path.write_text(_CLEAN_TEMPLATE)
    return str(file_path)


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
    """The djlint definition exposes the expected attribute values.

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
    """The djlint definition includes template file patterns.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("djlint")
    assert_that(plugin.definition.file_patterns).contains("*.jinja", "*.j2")


def test_check_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    messy_template: str,
) -> None:
    """DjLint check detects formatting issues in a messy template.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        messy_template: Path to a template with formatting issues.
    """
    plugin = get_plugin("djlint")
    result = plugin.check([messy_template], {})

    assert_that(result.name).is_equal_to("djlint")
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.success).is_false()


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    clean_template: str,
) -> None:
    """DjLint check passes on a well-formatted template.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        clean_template: Path to a well-formatted template.
    """
    plugin = get_plugin("djlint")
    result = plugin.check([clean_template], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """DjLint check handles an empty directory gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest temporary directory fixture.
    """
    plugin = get_plugin("djlint")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()


def test_fix_reformats_template(
    get_plugin: Callable[[str], BaseToolPlugin],
    messy_template: str,
) -> None:
    """DjLint fix reformats a messy template to a clean state.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        messy_template: Path to a template with formatting issues.
    """
    plugin = get_plugin("djlint")
    result = plugin.fix([messy_template], {})

    assert_that(result.name).is_equal_to("djlint")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_greater_than(0)
    assert_that(result.fixed_issues_count).is_greater_than(0)


def test_set_options_profile(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """The djlint profile option can be set and retrieved.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("djlint")
    plugin.set_options(profile="django")
    assert_that(plugin.options.get("profile")).is_equal_to("django")
