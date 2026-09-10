"""Integration tests for astro-check tool definition.

These tests require astro to be installed and available in PATH.
They verify the AstroCheckPlugin definition, check command, and set_options method.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool(
    "astro",
    launchers=(("bunx",), ("npx",)),
)


# --- Tests for AstroCheckPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "astro-check"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify AstroCheckPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    astro_check_plugin = get_plugin("astro-check")
    assert_that(getattr(astro_check_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify AstroCheckPlugin definition includes Astro file patterns.

    Tests that the plugin is configured to handle Astro files (*.astro).

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    astro_check_plugin = get_plugin("astro-check")
    assert_that(astro_check_plugin.definition.file_patterns).contains("*.astro")


def test_definition_has_version_command(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify AstroCheckPlugin definition has a version command.

    Tests that the plugin exposes a version command for checking
    the installed Astro version.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    astro_check_plugin = get_plugin("astro-check")
    assert_that(astro_check_plugin.definition.version_command).is_not_none()


# --- Integration tests for astro check command ---


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify astro check handles empty directories gracefully.

    Runs astro check on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    astro_check_plugin = get_plugin("astro-check")
    result = astro_check_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()


# --- Tests for AstroCheckPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("timeout", 60, 60),
        ("root", "./packages/web", "./packages/web"),
    ],
    ids=["timeout", "root"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify AstroCheckPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    astro_check_plugin = get_plugin("astro-check")
    astro_check_plugin.set_options(**{option_name: option_value})
    assert_that(astro_check_plugin.options.get(option_name)).is_equal_to(expected)


def test_set_exclude_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify AstroCheckPlugin.set_options correctly sets exclude_patterns.

    Tests that exclude patterns can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    astro_check_plugin = get_plugin("astro-check")
    astro_check_plugin.set_options(exclude_patterns=["node_modules", "dist"])
    assert_that(astro_check_plugin.exclude_patterns).contains("node_modules")
    assert_that(astro_check_plugin.exclude_patterns).contains("dist")
