"""Unit tests for svelte-check plugin options and definition."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.svelte_check import (
    SVELTE_CHECK_DEFAULT_PRIORITY,
    SVELTE_CHECK_DEFAULT_TIMEOUT,
    SVELTE_CHECK_FILE_PATTERNS,
    SvelteCheckPlugin,
)

# Tests for tool definition attributes


def test_definition_name(svelte_check_plugin: SvelteCheckPlugin) -> None:
    """Verify tool name is 'svelte-check'.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    assert_that(svelte_check_plugin.definition.name).is_equal_to("svelte-check")


def test_definition_description(svelte_check_plugin: SvelteCheckPlugin) -> None:
    """Verify tool has a description.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    assert_that(svelte_check_plugin.definition.description).is_not_empty()


def test_definition_can_fix_is_false(svelte_check_plugin: SvelteCheckPlugin) -> None:
    """Verify svelte-check cannot auto-fix issues.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    assert_that(svelte_check_plugin.definition.can_fix).is_false()


def test_definition_tool_type(svelte_check_plugin: SvelteCheckPlugin) -> None:
    """Verify tool type is LINTER | TYPE_CHECKER.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    expected_type = ToolType.LINTER | ToolType.TYPE_CHECKER
    assert_that(svelte_check_plugin.definition.tool_type).is_equal_to(expected_type)


def test_definition_file_patterns(svelte_check_plugin: SvelteCheckPlugin) -> None:
    """Verify file patterns include .svelte files.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    assert_that(svelte_check_plugin.definition.file_patterns).is_equal_to(
        SVELTE_CHECK_FILE_PATTERNS,
    )
    assert_that(svelte_check_plugin.definition.file_patterns).contains("*.svelte")


def test_definition_priority(svelte_check_plugin: SvelteCheckPlugin) -> None:
    """Verify tool priority is set correctly.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    assert_that(svelte_check_plugin.definition.priority).is_equal_to(
        SVELTE_CHECK_DEFAULT_PRIORITY,
    )


def test_definition_native_configs(svelte_check_plugin: SvelteCheckPlugin) -> None:
    """Verify native configs include svelte.config files.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    native_configs = svelte_check_plugin.definition.native_configs
    assert_that(native_configs).is_not_empty()
    assert_that(native_configs).contains("svelte.config.js")
    assert_that(native_configs).contains("svelte.config.ts")
    assert_that(native_configs).contains("svelte.config.mjs")


# Tests for default options


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", SVELTE_CHECK_DEFAULT_TIMEOUT),
        ("threshold", "error"),
        ("tsconfig", None),
    ],
    ids=[
        "timeout_equals_default",
        "threshold_is_error",
        "tsconfig_is_none",
    ],
)
def test_default_options_values(
    svelte_check_plugin: SvelteCheckPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        svelte_check_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# Tests for set_options method - valid options


def test_set_options_threshold_valid(
    svelte_check_plugin: SvelteCheckPlugin,
) -> None:
    """Set threshold option with valid string value.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    svelte_check_plugin.set_options(threshold="warning")
    assert_that(svelte_check_plugin.options.get("threshold")).is_equal_to("warning")


def test_set_options_tsconfig_valid(
    svelte_check_plugin: SvelteCheckPlugin,
) -> None:
    """Set tsconfig option with valid string path.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    svelte_check_plugin.set_options(tsconfig="./tsconfig.json")
    assert_that(svelte_check_plugin.options.get("tsconfig")).is_equal_to(
        "./tsconfig.json",
    )


def test_set_options_threshold_none(
    svelte_check_plugin: SvelteCheckPlugin,
) -> None:
    """Set threshold option to None (keeps default).

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    svelte_check_plugin.set_options(threshold=None)
    # None values are filtered out; threshold retains default "error"
    assert_that(svelte_check_plugin.options.get("threshold")).is_equal_to("error")


# Tests for set_options method - invalid types


def test_set_options_threshold_invalid_type(
    svelte_check_plugin: SvelteCheckPlugin,
) -> None:
    """Raise ValueError for invalid threshold type.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    with pytest.raises(ValueError, match="threshold must be a string"):
        svelte_check_plugin.set_options(threshold=123)  # type: ignore[arg-type]


def test_set_options_threshold_invalid_value(
    svelte_check_plugin: SvelteCheckPlugin,
) -> None:
    """Raise ValueError for invalid threshold value.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    with pytest.raises(
        ValueError,
        match="threshold must be 'error', 'warning', or 'hint'",
    ):
        svelte_check_plugin.set_options(threshold="invalid")


def test_set_options_tsconfig_invalid_type(
    svelte_check_plugin: SvelteCheckPlugin,
) -> None:
    """Raise ValueError for invalid tsconfig type.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    with pytest.raises(ValueError, match="tsconfig must be a string path"):
        svelte_check_plugin.set_options(tsconfig=123)  # type: ignore[arg-type]


# Tests for _build_command method


def test_build_command_basic(svelte_check_plugin: SvelteCheckPlugin) -> None:
    """Build basic command with default options.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    cmd = svelte_check_plugin._build_command()

    # Should contain machine-verbose output format
    assert_that(cmd).contains("--output")
    assert_that(cmd).contains("machine-verbose")
    # First element is the resolved svelte-check binary — possibly an absolute
    # node_modules/.bin path, and ``.cmd``-suffixed on Windows — or a bunx/npx
    # wrapper.
    assert_that(Path(cmd[0]).stem).is_in("svelte-check", "bunx", "npx")
    # The svelte-check package must be named somewhere in the command
    assert_that(" ".join(cmd)).contains("svelte-check")


def test_build_command_with_threshold(
    svelte_check_plugin: SvelteCheckPlugin,
) -> None:
    """Build command with threshold option.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    svelte_check_plugin.set_options(threshold="warning")
    cmd = svelte_check_plugin._build_command()

    assert_that(cmd).contains("--threshold")
    threshold_idx = cmd.index("--threshold")
    assert_that(cmd[threshold_idx + 1]).is_equal_to("warning")


def test_build_command_with_tsconfig(
    svelte_check_plugin: SvelteCheckPlugin,
) -> None:
    """Build command with tsconfig option.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
    """
    svelte_check_plugin.set_options(tsconfig="./tsconfig.app.json")
    cmd = svelte_check_plugin._build_command()

    assert_that(cmd).contains("--tsconfig")
    tsconfig_idx = cmd.index("--tsconfig")
    assert_that(cmd[tsconfig_idx + 1]).is_equal_to("./tsconfig.app.json")
