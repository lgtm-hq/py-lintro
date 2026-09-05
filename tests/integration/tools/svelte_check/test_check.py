"""Integration tests for svelte-check tool definition.

These tests require svelte-check to be installed and available in PATH.
They verify the SvelteCheckPlugin definition, check command, and set_options method.
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
    "svelte-check",
    launchers=(("bunx",), ("npx",)),
)


# --- Tests for SvelteCheckPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "svelte-check"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify SvelteCheckPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    svelte_check_plugin = get_plugin("svelte-check")
    assert_that(getattr(svelte_check_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify SvelteCheckPlugin definition includes Svelte file patterns.

    Tests that the plugin is configured to handle Svelte files (*.svelte).

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    svelte_check_plugin = get_plugin("svelte-check")
    assert_that(svelte_check_plugin.definition.file_patterns).contains("*.svelte")


def test_definition_has_version_command(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify SvelteCheckPlugin definition has a version command.

    Tests that the plugin exposes a version command for checking
    the installed svelte-check version.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    svelte_check_plugin = get_plugin("svelte-check")
    version_cmd = svelte_check_plugin.definition.version_command
    assert_that(version_cmd).is_not_none()
    assert_that(version_cmd).contains("--version")
    # Resolution may yield a local path, a PATH binary, or a pinned
    # ``bunx svelte-check@<version>`` spec (#1811); all name the package.
    assert_that(" ".join(version_cmd or [])).contains("svelte-check")


# --- Integration tests for svelte check command ---


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify svelte-check handles empty directories gracefully.

    Runs svelte-check on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    svelte_check_plugin = get_plugin("svelte-check")
    result = svelte_check_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


# --- Tests for SvelteCheckPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("timeout", 60, 60),
        ("threshold", "warning", "warning"),
        ("tsconfig", "./tsconfig.json", "./tsconfig.json"),
    ],
    ids=["timeout", "threshold", "tsconfig"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify SvelteCheckPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    svelte_check_plugin = get_plugin("svelte-check")
    svelte_check_plugin.set_options(**{option_name: option_value})
    assert_that(svelte_check_plugin.options.get(option_name)).is_equal_to(expected)


def test_set_exclude_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify SvelteCheckPlugin.set_options correctly sets exclude_patterns.

    Tests that exclude patterns can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    svelte_check_plugin = get_plugin("svelte-check")
    svelte_check_plugin.set_options(exclude_patterns=["node_modules", "dist"])
    assert_that(svelte_check_plugin.exclude_patterns).contains("node_modules")
    assert_that(svelte_check_plugin.exclude_patterns).contains("dist")
