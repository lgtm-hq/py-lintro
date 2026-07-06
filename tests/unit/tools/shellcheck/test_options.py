"""Unit tests for shellcheck plugin options and command building."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.shellcheck import (
    SHELLCHECK_DEFAULT_FORMAT,
    SHELLCHECK_DEFAULT_SEVERITY,
    SHELLCHECK_DEFAULT_TIMEOUT,
    SHELLCHECK_SEVERITY_LEVELS,
    SHELLCHECK_SHELL_DIALECTS,
    ShellcheckPlugin,
)
from lintro.utils.tool_options import parse_tool_options

# Tests for default option values


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", SHELLCHECK_DEFAULT_TIMEOUT),
        ("severity", SHELLCHECK_DEFAULT_SEVERITY),
        ("exclude", None),
        ("shell", None),
        ("external_sources", False),
        ("source_paths", None),
    ],
    ids=[
        "timeout_equals_default",
        "severity_equals_default",
        "exclude_is_none",
        "shell_is_none",
        "external_sources_is_false",
        "source_paths_is_none",
    ],
)
def test_default_options_values(
    shellcheck_plugin: ShellcheckPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        shellcheck_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# Tests for ShellcheckPlugin.set_options method - valid options


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("severity", "error"),
        ("severity", "warning"),
        ("severity", "info"),
        ("severity", "style"),
        ("exclude", ["SC2086", "SC2046"]),
        ("shell", "bash"),
        ("shell", "sh"),
        ("shell", "dash"),
        ("shell", "ksh"),
        ("external_sources", True),
        ("external_sources", False),
        ("source_paths", ["scripts/lib", "scripts/ci"]),
        ("source_paths", ["SCRIPTDIR"]),
    ],
    ids=[
        "severity_error",
        "severity_warning",
        "severity_info",
        "severity_style",
        "exclude_list",
        "shell_bash",
        "shell_sh",
        "shell_dash",
        "shell_ksh",
        "external_sources_true",
        "external_sources_false",
        "source_paths_list",
        "source_paths_scriptdir",
    ],
)
def test_set_options_valid(
    shellcheck_plugin: ShellcheckPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
        option_name: The name of the option to set.
        option_value: The value to set for the option.
    """
    shellcheck_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]
    assert_that(shellcheck_plugin.options.get(option_name)).is_equal_to(option_value)


def test_set_options_severity_case_insensitive(
    shellcheck_plugin: ShellcheckPlugin,
) -> None:
    """Set severity option is case insensitive.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    shellcheck_plugin.set_options(severity="WARNING")
    assert_that(shellcheck_plugin.options.get("severity")).is_equal_to("warning")


def test_set_options_shell_case_insensitive(
    shellcheck_plugin: ShellcheckPlugin,
) -> None:
    """Set shell option is case insensitive.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    # shell is dialect name, not subprocess shell=True
    shellcheck_plugin.set_options(shell="BASH")  # nosec B604
    assert_that(shellcheck_plugin.options.get("shell")).is_equal_to("bash")


# Tests for ShellcheckPlugin.set_options method - invalid types


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("severity", "critical", "Invalid severity level"),
        ("severity", "warn", "Invalid severity level"),
        ("shell", "fish", "Invalid shell dialect"),
        ("shell", "powershell", "Invalid shell dialect"),
        ("exclude", "SC2086", "exclude must be a list"),
        ("exclude", 123, "exclude must be a list"),
        ("external_sources", "yes", "external_sources must be a boolean"),
        ("external_sources", 1, "external_sources must be a boolean"),
        ("source_paths", 123, "source_paths must be a string or list"),
        ("source_paths", [1, 2], "source_paths must be a string or list"),
    ],
    ids=[
        "invalid_severity_critical",
        "invalid_severity_warn",
        "invalid_shell_fish",
        "invalid_shell_powershell",
        "invalid_exclude_string",
        "invalid_exclude_integer",
        "invalid_external_sources_string",
        "invalid_external_sources_integer",
        "invalid_source_paths_integer",
        "invalid_source_paths_list_of_ints",
    ],
)
def test_set_options_invalid_type(
    shellcheck_plugin: ShellcheckPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
        option_name: The name of the option being tested.
        invalid_value: An invalid value for the option.
        error_match: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=error_match):
        shellcheck_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]


# Tests for ShellcheckPlugin._build_command method


def test_build_command_basic(shellcheck_plugin: ShellcheckPlugin) -> None:
    """Build basic command without extra options.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    cmd = shellcheck_plugin._build_command()
    assert_that(cmd).contains("shellcheck")
    # Default format and severity should be included
    assert_that(cmd).contains("--format")
    assert_that(cmd).contains(SHELLCHECK_DEFAULT_FORMAT)
    assert_that(cmd).contains("--severity")
    assert_that(cmd).contains(SHELLCHECK_DEFAULT_SEVERITY)


def test_build_command_with_severity(shellcheck_plugin: ShellcheckPlugin) -> None:
    """Build command with severity option.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    shellcheck_plugin.set_options(severity="error")
    cmd = shellcheck_plugin._build_command()

    assert_that(cmd).contains("--severity")
    severity_idx = cmd.index("--severity")
    assert_that(cmd[severity_idx + 1]).is_equal_to("error")


def test_build_command_with_exclude_codes(shellcheck_plugin: ShellcheckPlugin) -> None:
    """Build command with exclude option.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    shellcheck_plugin.set_options(exclude=["SC2086", "SC2046"])
    cmd = shellcheck_plugin._build_command()

    assert_that(cmd).contains("--exclude")
    # Each exclude code should be passed separately
    exclude_indices = [i for i, x in enumerate(cmd) if x == "--exclude"]
    assert_that(exclude_indices).is_length(2)
    assert_that(cmd[exclude_indices[0] + 1]).is_equal_to("SC2086")
    assert_that(cmd[exclude_indices[1] + 1]).is_equal_to("SC2046")


def test_build_command_with_shell_dialect(shellcheck_plugin: ShellcheckPlugin) -> None:
    """Build command with shell dialect option.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    # shell is dialect name, not subprocess shell=True
    shellcheck_plugin.set_options(shell="bash")  # nosec B604
    cmd = shellcheck_plugin._build_command()

    assert_that(cmd).contains("--shell")
    shell_idx = cmd.index("--shell")
    assert_that(cmd[shell_idx + 1]).is_equal_to("bash")


def test_build_command_no_source_following_by_default(
    shellcheck_plugin: ShellcheckPlugin,
) -> None:
    """Source-following flags are absent unless explicitly enabled.

    This guards backward compatibility for projects that do not opt in.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    cmd = shellcheck_plugin._build_command()
    assert_that(cmd).does_not_contain("--external-sources")
    assert_that(cmd).does_not_contain("-x")
    assert_that(any(arg.startswith("--source-path=") for arg in cmd)).is_false()


def test_build_command_with_external_sources(
    shellcheck_plugin: ShellcheckPlugin,
) -> None:
    """Build command with external_sources enables the -x flag.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    shellcheck_plugin.set_options(external_sources=True)
    cmd = shellcheck_plugin._build_command()
    assert_that(cmd).contains("--external-sources")


def test_build_command_external_sources_false_omits_flag(
    shellcheck_plugin: ShellcheckPlugin,
) -> None:
    """external_sources=False does not add the -x flag.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    shellcheck_plugin.set_options(external_sources=False)
    cmd = shellcheck_plugin._build_command()
    assert_that(cmd).does_not_contain("--external-sources")


def test_build_command_with_source_paths(
    shellcheck_plugin: ShellcheckPlugin,
) -> None:
    """Build command emits one --source-path=... per configured path.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    shellcheck_plugin.set_options(source_paths=["SCRIPTDIR", "scripts/lib"])
    cmd = shellcheck_plugin._build_command()
    assert_that(cmd).contains("--source-path=SCRIPTDIR")
    assert_that(cmd).contains("--source-path=scripts/lib")


def test_build_command_with_all_options(shellcheck_plugin: ShellcheckPlugin) -> None:
    """Build command with all options set.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    shellcheck_plugin.set_options(
        severity="warning",
        exclude=["SC2086"],
        # shell is dialect name, not subprocess shell=True
        shell="ksh",  # nosec B604
    )
    cmd = shellcheck_plugin._build_command()

    assert_that(cmd).contains("--format")
    assert_that(cmd).contains("--severity")
    assert_that(cmd).contains("--exclude")
    assert_that(cmd).contains("--shell")

    # Verify correct values
    severity_idx = cmd.index("--severity")
    assert_that(cmd[severity_idx + 1]).is_equal_to("warning")

    shell_idx = cmd.index("--shell")
    assert_that(cmd[shell_idx + 1]).is_equal_to("ksh")


# Tests for constants


def test_severity_levels_constant() -> None:
    """Verify SHELLCHECK_SEVERITY_LEVELS contains expected values."""
    assert_that(SHELLCHECK_SEVERITY_LEVELS).contains("error")
    assert_that(SHELLCHECK_SEVERITY_LEVELS).contains("warning")
    assert_that(SHELLCHECK_SEVERITY_LEVELS).contains("info")
    assert_that(SHELLCHECK_SEVERITY_LEVELS).contains("style")
    assert_that(SHELLCHECK_SEVERITY_LEVELS).is_length(4)


def test_shell_dialects_constant() -> None:
    """Verify SHELLCHECK_SHELL_DIALECTS contains expected values."""
    assert_that(SHELLCHECK_SHELL_DIALECTS).contains("bash")
    assert_that(SHELLCHECK_SHELL_DIALECTS).contains("sh")
    assert_that(SHELLCHECK_SHELL_DIALECTS).contains("dash")
    assert_that(SHELLCHECK_SHELL_DIALECTS).contains("ksh")
    assert_that(SHELLCHECK_SHELL_DIALECTS).is_length(4)


def test_default_timeout_constant() -> None:
    """Verify SHELLCHECK_DEFAULT_TIMEOUT is 30."""
    assert_that(SHELLCHECK_DEFAULT_TIMEOUT).is_equal_to(30)


def test_default_format_constant() -> None:
    """Verify SHELLCHECK_DEFAULT_FORMAT is json1."""
    assert_that(SHELLCHECK_DEFAULT_FORMAT).is_equal_to("json1")


def test_default_severity_constant() -> None:
    """Verify SHELLCHECK_DEFAULT_SEVERITY is style."""
    assert_that(SHELLCHECK_DEFAULT_SEVERITY).is_equal_to("style")


# Tests for parsing source-following options from CLI --tool-options


def test_set_options_source_paths_string_normalized_to_list(
    shellcheck_plugin: ShellcheckPlugin,
) -> None:
    """A single string source_paths is normalized to a one-element list.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    shellcheck_plugin.set_options(source_paths="SCRIPTDIR")
    assert_that(shellcheck_plugin.options.get("source_paths")).is_equal_to(
        ["SCRIPTDIR"],
    )


def test_parse_external_sources_from_cli() -> None:
    """external_sources is coerced to a bool from CLI --tool-options."""
    parsed = parse_tool_options("shellcheck:external_sources=True")
    assert_that(parsed).contains_key("shellcheck")
    assert_that(parsed["shellcheck"].get("external_sources")).is_equal_to(True)


def test_parse_source_paths_from_cli() -> None:
    """source_paths is coerced to a list from pipe-delimited --tool-options."""
    parsed = parse_tool_options("shellcheck:source_paths=SCRIPTDIR|scripts/lib")
    assert_that(parsed["shellcheck"].get("source_paths")).is_equal_to(
        ["SCRIPTDIR", "scripts/lib"],
    )


def test_parse_source_following_options_flow_into_command() -> None:
    """CLI-parsed options wire through set_options into the built command."""
    parsed = parse_tool_options(
        "shellcheck:external_sources=True,shellcheck:source_paths=SCRIPTDIR",
    )
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        plugin = ShellcheckPlugin()
    plugin.set_options(**parsed["shellcheck"])  # type: ignore[arg-type]
    cmd = plugin._build_command()
    assert_that(cmd).contains("--external-sources")
    assert_that(cmd).contains("--source-path=SCRIPTDIR")
