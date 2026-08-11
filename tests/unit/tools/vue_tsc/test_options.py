"""Unit tests for vue-tsc plugin options and definition."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.vue_tsc import (
    VUE_TSC_DEFAULT_PRIORITY,
    VUE_TSC_DEFAULT_TIMEOUT,
    VUE_TSC_FILE_PATTERNS,
    VueTscPlugin,
)

# Tests for tool definition attributes


def test_definition_name(vue_tsc_plugin: VueTscPlugin) -> None:
    """Verify tool name is 'vue-tsc'.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    assert_that(vue_tsc_plugin.definition.name).is_equal_to("vue-tsc")


def test_definition_description(vue_tsc_plugin: VueTscPlugin) -> None:
    """Verify tool has a description.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    assert_that(vue_tsc_plugin.definition.description).is_not_empty()


def test_definition_can_fix_is_false(vue_tsc_plugin: VueTscPlugin) -> None:
    """Verify vue-tsc cannot auto-fix issues.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    assert_that(vue_tsc_plugin.definition.can_fix).is_false()


def test_definition_tool_type(vue_tsc_plugin: VueTscPlugin) -> None:
    """Verify tool type is LINTER | TYPE_CHECKER.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    expected_type = ToolType.LINTER | ToolType.TYPE_CHECKER
    assert_that(vue_tsc_plugin.definition.tool_type).is_equal_to(expected_type)


def test_definition_file_patterns(vue_tsc_plugin: VueTscPlugin) -> None:
    """Verify file patterns include .vue files.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    assert_that(vue_tsc_plugin.definition.file_patterns).is_equal_to(
        VUE_TSC_FILE_PATTERNS,
    )
    assert_that(vue_tsc_plugin.definition.file_patterns).contains("*.vue")


def test_definition_priority(vue_tsc_plugin: VueTscPlugin) -> None:
    """Verify tool priority is set correctly.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    assert_that(vue_tsc_plugin.definition.priority).is_equal_to(
        VUE_TSC_DEFAULT_PRIORITY,
    )


def test_definition_native_configs(vue_tsc_plugin: VueTscPlugin) -> None:
    """Verify native configs include tsconfig files.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    native_configs = vue_tsc_plugin.definition.native_configs
    assert_that(native_configs).is_not_empty()
    assert_that(native_configs).contains("tsconfig.json")
    assert_that(native_configs).contains("tsconfig.app.json")


# Tests for default options


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", VUE_TSC_DEFAULT_TIMEOUT),
        ("project", None),
        ("strict", None),
        ("skip_lib_check", True),
        ("use_project_files", False),
    ],
    ids=[
        "timeout_equals_default",
        "project_is_none",
        "strict_is_none",
        "skip_lib_check_is_true",
        "use_project_files_is_false",
    ],
)
def test_default_options_values(
    vue_tsc_plugin: VueTscPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        vue_tsc_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# Tests for set_options method - valid options


def test_set_options_project_valid(vue_tsc_plugin: VueTscPlugin) -> None:
    """Set project option with valid string path.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    vue_tsc_plugin.set_options(project="tsconfig.app.json")
    assert_that(vue_tsc_plugin.options.get("project")).is_equal_to("tsconfig.app.json")


def test_set_options_project_none(vue_tsc_plugin: VueTscPlugin) -> None:
    """Set project option to None (default).

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    vue_tsc_plugin.set_options(project=None)
    # None values are filtered out, so key won't exist
    assert_that(vue_tsc_plugin.options.get("project")).is_none()


def test_set_options_strict_true(vue_tsc_plugin: VueTscPlugin) -> None:
    """Set strict option to True.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    vue_tsc_plugin.set_options(strict=True)
    assert_that(vue_tsc_plugin.options.get("strict")).is_true()


def test_set_options_skip_lib_check_false(vue_tsc_plugin: VueTscPlugin) -> None:
    """Set skip_lib_check option to False.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    vue_tsc_plugin.set_options(skip_lib_check=False)
    assert_that(vue_tsc_plugin.options.get("skip_lib_check")).is_false()


# Tests for set_options method - invalid types


def test_set_options_project_invalid_type(vue_tsc_plugin: VueTscPlugin) -> None:
    """Raise ValueError for invalid project type.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    with pytest.raises(ValueError, match="project must be a string path"):
        vue_tsc_plugin.set_options(project=123)  # type: ignore[arg-type]


def test_set_options_strict_invalid_type(vue_tsc_plugin: VueTscPlugin) -> None:
    """Raise ValueError for invalid strict type.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    with pytest.raises(ValueError, match="strict must be a boolean"):
        vue_tsc_plugin.set_options(strict="yes")  # type: ignore[arg-type]


def test_set_options_skip_lib_check_invalid_type(
    vue_tsc_plugin: VueTscPlugin,
) -> None:
    """Raise ValueError for invalid skip_lib_check type.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    with pytest.raises(ValueError, match="skip_lib_check must be a boolean"):
        vue_tsc_plugin.set_options(skip_lib_check="no")  # type: ignore[arg-type]


def test_set_options_use_project_files_invalid_type(
    vue_tsc_plugin: VueTscPlugin,
) -> None:
    """Raise ValueError for invalid use_project_files type.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    with pytest.raises(ValueError, match="use_project_files must be a boolean"):
        vue_tsc_plugin.set_options(use_project_files="yes")  # type: ignore[arg-type]


# Tests for _build_command method


def test_build_command_basic(vue_tsc_plugin: VueTscPlugin) -> None:
    """Build basic command with default options.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    cmd = vue_tsc_plugin._build_command(files=[])

    # Should contain --noEmit and --pretty false
    assert_that(cmd).contains("--noEmit")
    assert_that(cmd).contains("--pretty")
    assert_that(cmd).contains("false")
    # First element is the resolved vue-tsc binary (possibly a project-local
    # path) or a bunx/npx wrapper.
    assert_that(Path(cmd[0]).name).is_in("vue-tsc", "bunx", "npx")


def test_build_command_with_project(vue_tsc_plugin: VueTscPlugin) -> None:
    """Build command with project option.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    cmd = vue_tsc_plugin._build_command(
        files=[],
        project_path="/path/to/tsconfig.json",
    )

    assert_that(cmd).contains("--project")
    project_idx = cmd.index("--project")
    assert_that(cmd[project_idx + 1]).is_equal_to("/path/to/tsconfig.json")


def test_build_command_with_strict(vue_tsc_plugin: VueTscPlugin) -> None:
    """Build command with strict option enabled.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    vue_tsc_plugin.set_options(strict=True)
    cmd = vue_tsc_plugin._build_command(files=[])

    assert_that(cmd).contains("--strict")


def test_build_command_with_skip_lib_check(vue_tsc_plugin: VueTscPlugin) -> None:
    """Build command includes --skipLibCheck by default.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    cmd = vue_tsc_plugin._build_command(files=[])

    assert_that(cmd).contains("--skipLibCheck")


def test_build_command_without_skip_lib_check(vue_tsc_plugin: VueTscPlugin) -> None:
    """Build command without --skipLibCheck when disabled.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
    """
    vue_tsc_plugin.set_options(skip_lib_check=False)
    cmd = vue_tsc_plugin._build_command(files=[])

    assert_that(cmd).does_not_contain("--skipLibCheck")
