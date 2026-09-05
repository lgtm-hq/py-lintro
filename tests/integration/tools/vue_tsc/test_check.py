"""Integration tests for vue-tsc tool definition.

These tests require vue-tsc to be installed and available in PATH.
They verify the VueTscPlugin definition, check command, and set_options method.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration._tools import NO_MIN_VERSION, require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# `vue-tsc --version` prints the bundled TypeScript version, not vue-tsc's
# (which is why the manifest resolves it through a script), so there is no
# meaningful floor to compare the output against.
pytestmark = require_tool(
    "vue-tsc",
    launchers=(("bunx",), ("npx",)),
    min_version=NO_MIN_VERSION,
)


# --- Tests for VueTscPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "vue-tsc"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify VueTscPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    vue_tsc_plugin = get_plugin("vue-tsc")
    assert_that(getattr(vue_tsc_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify VueTscPlugin definition includes Vue file patterns.

    Tests that the plugin is configured to handle Vue files (*.vue).

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    vue_tsc_plugin = get_plugin("vue-tsc")
    assert_that(vue_tsc_plugin.definition.file_patterns).contains("*.vue")


def test_definition_has_version_command(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify VueTscPlugin definition has a version command.

    Tests that the plugin exposes a version command for checking
    the installed vue-tsc version.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    vue_tsc_plugin = get_plugin("vue-tsc")
    assert_that(vue_tsc_plugin.definition.version_command).is_not_none()


# --- Integration tests for vue-tsc check command ---


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify vue-tsc check handles empty directories gracefully.

    Runs vue-tsc check on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    vue_tsc_plugin = get_plugin("vue-tsc")
    result = vue_tsc_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


# --- Tests for VueTscPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("timeout", 60, 60),
        ("project", "./tsconfig.app.json", "./tsconfig.app.json"),
        ("strict", True, True),
        ("skip_lib_check", False, False),
    ],
    ids=["timeout", "project", "strict", "skip_lib_check"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify VueTscPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    vue_tsc_plugin = get_plugin("vue-tsc")
    vue_tsc_plugin.set_options(**{option_name: option_value})
    assert_that(vue_tsc_plugin.options.get(option_name)).is_equal_to(expected)


def test_set_exclude_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify VueTscPlugin.set_options correctly sets exclude_patterns.

    Tests that exclude patterns can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    vue_tsc_plugin = get_plugin("vue-tsc")
    vue_tsc_plugin.set_options(exclude_patterns=["node_modules", "dist"])
    assert_that(vue_tsc_plugin.exclude_patterns).contains("node_modules")
    assert_that(vue_tsc_plugin.exclude_patterns).contains("dist")
