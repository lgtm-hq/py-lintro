"""Unit tests for astro-check plugin options and definition."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.astro_check import (
    ASTRO_CHECK_DEFAULT_PRIORITY,
    ASTRO_CHECK_DEFAULT_TIMEOUT,
    ASTRO_CHECK_FILE_PATTERNS,
    AstroCheckPlugin,
)

# Tests for tool definition attributes


def test_definition_name(astro_check_plugin: AstroCheckPlugin) -> None:
    """Verify tool name is 'astro-check'.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    assert_that(astro_check_plugin.definition.name).is_equal_to("astro-check")


def test_definition_description(astro_check_plugin: AstroCheckPlugin) -> None:
    """Verify tool has a description.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    assert_that(astro_check_plugin.definition.description).is_not_empty()


def test_definition_can_fix_is_false(astro_check_plugin: AstroCheckPlugin) -> None:
    """Verify astro-check cannot auto-fix issues.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    assert_that(astro_check_plugin.definition.can_fix).is_false()


def test_definition_tool_type(astro_check_plugin: AstroCheckPlugin) -> None:
    """Verify tool type is LINTER | TYPE_CHECKER.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    expected_type = ToolType.LINTER | ToolType.TYPE_CHECKER
    assert_that(astro_check_plugin.definition.tool_type).is_equal_to(expected_type)


def test_definition_file_patterns(astro_check_plugin: AstroCheckPlugin) -> None:
    """Verify file patterns include .astro files.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    assert_that(astro_check_plugin.definition.file_patterns).is_equal_to(
        ASTRO_CHECK_FILE_PATTERNS,
    )
    assert_that(astro_check_plugin.definition.file_patterns).contains("*.astro")


def test_definition_priority(astro_check_plugin: AstroCheckPlugin) -> None:
    """Verify tool priority is set correctly.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    assert_that(astro_check_plugin.definition.priority).is_equal_to(
        ASTRO_CHECK_DEFAULT_PRIORITY,
    )


def test_definition_native_configs(astro_check_plugin: AstroCheckPlugin) -> None:
    """Verify native configs include astro.config files.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    native_configs = astro_check_plugin.definition.native_configs
    assert_that(native_configs).is_not_empty()
    assert_that(native_configs).contains("astro.config.mjs")
    assert_that(native_configs).contains("astro.config.ts")
    assert_that(native_configs).contains("astro.config.js")


# Tests for default options


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", ASTRO_CHECK_DEFAULT_TIMEOUT),
        ("root", None),
    ],
    ids=[
        "timeout_equals_default",
        "root_is_none",
    ],
)
def test_default_options_values(
    astro_check_plugin: AstroCheckPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        astro_check_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# Tests for set_options method - valid options


def test_set_options_root_valid(astro_check_plugin: AstroCheckPlugin) -> None:
    """Set root option with valid string path.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    astro_check_plugin.set_options(root="/path/to/project")
    assert_that(astro_check_plugin.options.get("root")).is_equal_to("/path/to/project")


def test_set_options_root_none(astro_check_plugin: AstroCheckPlugin) -> None:
    """Set root option to None (default).

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    astro_check_plugin.set_options(root=None)
    # None values are filtered out, so key won't exist
    assert_that(astro_check_plugin.options.get("root")).is_none()


# Tests for set_options method - invalid types


def test_set_options_root_invalid_type(astro_check_plugin: AstroCheckPlugin) -> None:
    """Raise ValueError for invalid root type.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    with pytest.raises(ValueError, match="root must be a string path"):
        astro_check_plugin.set_options(root=123)  # type: ignore[arg-type]


def test_set_options_root_invalid_bool(astro_check_plugin: AstroCheckPlugin) -> None:
    """Raise ValueError for boolean root.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    with pytest.raises(ValueError, match="root must be a string path"):
        astro_check_plugin.set_options(root=True)  # type: ignore[arg-type]


# Tests for _build_command method


def test_build_command_basic(
    astro_check_plugin: AstroCheckPlugin,
    no_local_node_install: None,
) -> None:
    """Build basic command with default options.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
        no_local_node_install: Fixture removing any project-local Node install
            from resolution.
    """
    cmd = astro_check_plugin._build_command()

    # Should contain astro and check subcommand
    assert_that(cmd).contains("check")
    # First element is the PATH/runner-resolved astro binary or a bunx/npx
    # wrapper — not a project-local node_modules path.
    assert_that(Path(cmd[0]).name).is_in("astro", "bunx", "npx")


def test_build_command_with_root(astro_check_plugin: AstroCheckPlugin) -> None:
    """Build command with root option.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
    """
    astro_check_plugin.set_options(root="/path/to/project")
    cmd = astro_check_plugin._build_command()

    assert_that(cmd).contains("--root")
    root_idx = cmd.index("--root")
    assert_that(cmd[root_idx + 1]).is_equal_to("/path/to/project")
