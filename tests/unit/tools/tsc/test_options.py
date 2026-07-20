"""Unit tests for tsc plugin options and command building."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.tsc import (
    TSC_DEFAULT_PRIORITY,
    TSC_DEFAULT_TIMEOUT,
    TSC_FILE_PATTERNS,
    TscPlugin,
)

# =============================================================================
# Tests for TscPlugin definition
# =============================================================================


def test_definition_name(tsc_plugin: TscPlugin) -> None:
    """Plugin name is 'tsc'.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    assert_that(tsc_plugin.definition.name).is_equal_to("tsc")


def test_definition_description(tsc_plugin: TscPlugin) -> None:
    """Plugin has a description containing TypeScript.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    assert_that(tsc_plugin.definition.description).contains("TypeScript")


def test_definition_can_fix(tsc_plugin: TscPlugin) -> None:
    """Plugin cannot fix issues.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    assert_that(tsc_plugin.definition.can_fix).is_false()


def test_definition_tool_type(tsc_plugin: TscPlugin) -> None:
    """Plugin is a linter and type checker.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    tool_type = tsc_plugin.definition.tool_type
    assert_that(ToolType.LINTER in tool_type).is_true()
    assert_that(ToolType.TYPE_CHECKER in tool_type).is_true()


def test_definition_file_patterns(tsc_plugin: TscPlugin) -> None:
    """Plugin handles TypeScript and JavaScript files.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    patterns = tsc_plugin.definition.file_patterns
    assert_that(patterns).is_equal_to(TSC_FILE_PATTERNS)
    assert_that(patterns).contains(
        "*.ts",
        "*.tsx",
        "*.mts",
        "*.cts",
        "*.js",
        "*.mjs",
        "*.cjs",
        "*.jsx",
    )


def test_definition_native_configs(tsc_plugin: TscPlugin) -> None:
    """Plugin recognizes tsconfig.json.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    assert_that(tsc_plugin.definition.native_configs).contains("tsconfig.json")


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", TSC_DEFAULT_TIMEOUT),
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
    tsc_plugin: TscPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(tsc_plugin.definition.default_options).contains_key(option_name)
    assert_that(tsc_plugin.definition.default_options[option_name]).is_equal_to(
        expected_value,
    )


def test_definition_priority(tsc_plugin: TscPlugin) -> None:
    """Plugin has correct priority.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    assert_that(tsc_plugin.definition.priority).is_equal_to(TSC_DEFAULT_PRIORITY)


# =============================================================================
# Tests for TscPlugin.set_options method
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("project", "tsconfig.json"),
        ("project", "tsconfig.build.json"),
        ("strict", True),
        ("strict", False),
        ("skip_lib_check", True),
        ("skip_lib_check", False),
        ("use_project_files", True),
        ("use_project_files", False),
    ],
    ids=[
        "project_default",
        "project_custom",
        "strict_true",
        "strict_false",
        "skip_lib_check_true",
        "skip_lib_check_false",
        "use_project_files_true",
        "use_project_files_false",
    ],
)
def test_set_options_valid(
    tsc_plugin: TscPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        option_name: The name of the option to set.
        option_value: The value to set for the option.
    """
    tsc_plugin.set_options(
        **{option_name: option_value},  # type: ignore[arg-type]  # Dynamic kwargs
    )
    assert_that(tsc_plugin.options.get(option_name)).is_equal_to(option_value)


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("project", 123, "project must be a string path"),
        ("strict", "yes", "strict must be a boolean"),
        ("skip_lib_check", "yes", "skip_lib_check must be a boolean"),
        ("use_project_files", "yes", "use_project_files must be a boolean"),
    ],
    ids=[
        "invalid_project_type",
        "invalid_strict_type",
        "invalid_skip_lib_check_type",
        "invalid_use_project_files_type",
    ],
)
def test_set_options_invalid_type(
    tsc_plugin: TscPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        option_name: The name of the option being tested.
        invalid_value: An invalid value for the option.
        error_match: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=error_match):
        tsc_plugin.set_options(
            **{option_name: invalid_value},  # type: ignore[arg-type]  # Intentional wrong type
        )


# =============================================================================
# Tests for TscPlugin._get_tsc_command method
# =============================================================================


def test_get_tsc_command_with_tsc_available(tsc_plugin: TscPlugin) -> None:
    """Return direct tsc command when available.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    with patch("shutil.which", return_value="/usr/bin/tsc"):
        cmd = tsc_plugin._get_tsc_command()

    assert_that(cmd).is_equal_to(["tsc"])


def test_get_tsc_command_with_bunx_fallback(tsc_plugin: TscPlugin) -> None:
    """Fall back to bunx when tsc not directly available.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """

    def which_side_effect(cmd: str) -> str | None:
        if cmd == "tsc":
            return None
        if cmd == "bunx":
            return "/usr/bin/bunx"
        return None

    with patch("shutil.which", side_effect=which_side_effect):
        cmd = tsc_plugin._get_tsc_command()

    assert_that(cmd).is_equal_to(["bunx", "tsc"])


def test_get_tsc_command_with_npx_fallback(tsc_plugin: TscPlugin) -> None:
    """Fall back to npx when tsc and bunx not available.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """

    def which_side_effect(cmd: str) -> str | None:
        if cmd == "npx":
            return "/usr/bin/npx"
        return None

    with patch("shutil.which", side_effect=which_side_effect):
        cmd = tsc_plugin._get_tsc_command()

    assert_that(cmd).is_equal_to(["npx", "tsc"])


def test_get_tsc_command_fallback_to_tsc(tsc_plugin: TscPlugin) -> None:
    """Fall back to tsc when nothing else available.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    with patch("shutil.which", return_value=None):
        cmd = tsc_plugin._get_tsc_command()

    assert_that(cmd).is_equal_to(["tsc"])


# =============================================================================
# Tests for TscPlugin._build_command method
# =============================================================================


def test_build_command_default(tsc_plugin: TscPlugin) -> None:
    """Build command with default options.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    with patch.object(tsc_plugin, "_get_tsc_command", return_value=["tsc"]):
        cmd = tsc_plugin._build_command(files=["src/main.ts"])

    assert_that(cmd).contains("tsc")
    assert_that(cmd).contains("--noEmit")
    assert_that(cmd).contains("--pretty", "false")
    assert_that(cmd).contains("--skipLibCheck")
    assert_that(cmd).contains("src/main.ts")


def test_build_command_with_project(tsc_plugin: TscPlugin) -> None:
    """Build command with project option.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    with patch.object(tsc_plugin, "_get_tsc_command", return_value=["tsc"]):
        cmd = tsc_plugin._build_command(
            files=[],
            project_path="tsconfig.build.json",
        )

    assert_that(cmd).contains("--project")
    assert_that(cmd).contains("tsconfig.build.json")


def test_build_command_with_strict(tsc_plugin: TscPlugin) -> None:
    """Build command with strict mode enabled.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    tsc_plugin.set_options(strict=True)
    with patch.object(tsc_plugin, "_get_tsc_command", return_value=["tsc"]):
        cmd = tsc_plugin._build_command(files=["src/main.ts"])

    assert_that(cmd).contains("--strict")


def test_build_command_without_strict(tsc_plugin: TscPlugin) -> None:
    """Build command with strict mode disabled.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    tsc_plugin.set_options(strict=False)
    with patch.object(tsc_plugin, "_get_tsc_command", return_value=["tsc"]):
        cmd = tsc_plugin._build_command(files=["src/main.ts"])

    # --strict is off by default in tsc, so no flag is emitted when strict=False
    assert_that("--strict" in cmd).is_false()


def test_build_command_without_skip_lib_check(tsc_plugin: TscPlugin) -> None:
    """Build command without skip lib check.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    tsc_plugin.set_options(skip_lib_check=False)
    with patch.object(tsc_plugin, "_get_tsc_command", return_value=["tsc"]):
        cmd = tsc_plugin._build_command(files=["src/main.ts"])

    assert_that("--skipLibCheck" in cmd).is_false()
