"""Unit tests for gitleaks plugin options and command building."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.gitleaks.definition import (
    GITLEAKS_DEFAULT_TIMEOUT,
    GITLEAKS_OUTPUT_FORMAT,
    GitleaksPlugin,
)

# Tests for default options


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", GITLEAKS_DEFAULT_TIMEOUT),
        ("no_git", True),
        ("config", None),
        ("baseline_path", None),
        ("redact", True),
        ("max_target_megabytes", None),
    ],
    ids=[
        "timeout_equals_default",
        "no_git_is_true",
        "config_is_none",
        "baseline_path_is_none",
        "redact_is_true",
        "max_target_megabytes_is_none",
    ],
)
def test_default_options_values(
    gitleaks_plugin: GitleaksPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        gitleaks_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# Tests for GitleaksPlugin.set_options method - valid options


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("no_git", False),
        ("config", "/path/to/.gitleaks.toml"),
        ("baseline_path", "/path/to/baseline.json"),
        ("redact", False),
        ("max_target_megabytes", 100),
    ],
    ids=[
        "no_git_false",
        "config_path",
        "baseline_path",
        "redact_false",
        "max_target_megabytes_100",
    ],
)
def test_set_options_valid(
    gitleaks_plugin: GitleaksPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        option_name: The name of the option to set.
        option_value: The value to set for the option.
    """
    gitleaks_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]
    assert_that(gitleaks_plugin.options.get(option_name)).is_equal_to(option_value)


# Tests for GitleaksPlugin.set_options method - invalid types


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("no_git", "yes", "no_git must be a boolean"),
        ("no_git", 1, "no_git must be a boolean"),
        ("config", 123, "config must be a string"),
        ("config", True, "config must be a string"),
        ("baseline_path", 456, "baseline_path must be a string"),
        ("max_target_megabytes", "large", "max_target_megabytes must be an integer"),
        ("max_target_megabytes", -1, "max_target_megabytes must be positive"),
        ("max_target_megabytes", 0, "max_target_megabytes must be positive"),
        ("redact", "true", "redact must be a boolean"),
    ],
    ids=[
        "invalid_no_git_string",
        "invalid_no_git_int",
        "invalid_config_int",
        "invalid_config_bool",
        "invalid_baseline_path_int",
        "invalid_max_target_megabytes_string",
        "invalid_max_target_megabytes_negative",
        "invalid_max_target_megabytes_zero",
        "invalid_redact_string",
    ],
)
def test_set_options_invalid_type(
    gitleaks_plugin: GitleaksPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        option_name: The name of the option being tested.
        invalid_value: An invalid value for the option.
        error_match: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=error_match):
        gitleaks_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]


# Tests for GitleaksPlugin._build_check_command method


def test_build_check_command_basic(gitleaks_plugin: GitleaksPlugin) -> None:
    """Build basic command with default options.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
    """
    cmd = gitleaks_plugin._build_check_command(
        source_path="/path/to/source",
        report_path="/tmp/report.json",
    )

    assert_that(cmd).contains("gitleaks")
    assert_that(cmd).contains("detect")
    assert_that(cmd).contains("--source")
    assert_that(cmd).contains("/path/to/source")
    # Default no_git=True should add --no-git
    assert_that(cmd).contains("--no-git")
    # Default redact=True should add --redact
    assert_that(cmd).contains("--redact")
    # Output format should be JSON
    assert_that(cmd).contains("--report-format")
    assert_that(cmd).contains(GITLEAKS_OUTPUT_FORMAT)
    # Report path should be included
    assert_that(cmd).contains("--report-path")
    assert_that(cmd).contains("/tmp/report.json")


def test_build_check_command_with_no_git_false(gitleaks_plugin: GitleaksPlugin) -> None:
    """Build command without --no-git flag when no_git=False.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
    """
    gitleaks_plugin.set_options(no_git=False)
    cmd = gitleaks_plugin._build_check_command(
        source_path="/path/to/source",
        report_path="/tmp/report.json",
    )

    assert_that(cmd).does_not_contain("--no-git")


def test_build_check_command_with_config(gitleaks_plugin: GitleaksPlugin) -> None:
    """Build command with config file path.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
    """
    gitleaks_plugin.set_options(config="/path/to/.gitleaks.toml")
    cmd = gitleaks_plugin._build_check_command(
        source_path="/path/to/source",
        report_path="/tmp/report.json",
    )

    assert_that(cmd).contains("--config")
    config_idx = cmd.index("--config")
    assert_that(cmd[config_idx + 1]).is_equal_to("/path/to/.gitleaks.toml")


def test_build_check_command_with_baseline_path(
    gitleaks_plugin: GitleaksPlugin,
) -> None:
    """Build command with baseline path.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
    """
    gitleaks_plugin.set_options(baseline_path="/path/to/baseline.json")
    cmd = gitleaks_plugin._build_check_command(
        source_path="/path/to/source",
        report_path="/tmp/report.json",
    )

    assert_that(cmd).contains("--baseline-path")
    baseline_idx = cmd.index("--baseline-path")
    assert_that(cmd[baseline_idx + 1]).is_equal_to("/path/to/baseline.json")


def test_build_check_command_with_max_target_megabytes(
    gitleaks_plugin: GitleaksPlugin,
) -> None:
    """Build command with max target megabytes.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
    """
    gitleaks_plugin.set_options(max_target_megabytes=100)
    cmd = gitleaks_plugin._build_check_command(
        source_path="/path/to/source",
        report_path="/tmp/report.json",
    )

    assert_that(cmd).contains("--max-target-megabytes")
    max_mb_idx = cmd.index("--max-target-megabytes")
    assert_that(cmd[max_mb_idx + 1]).is_equal_to("100")


def test_build_check_command_with_redact_false(
    gitleaks_plugin: GitleaksPlugin,
) -> None:
    """Build command without --redact flag when redact=False.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
    """
    gitleaks_plugin.set_options(redact=False)
    cmd = gitleaks_plugin._build_check_command(
        source_path="/path/to/source",
        report_path="/tmp/report.json",
    )

    assert_that(cmd).does_not_contain("--redact")


def test_build_check_command_with_all_options(gitleaks_plugin: GitleaksPlugin) -> None:
    """Build command with all options set.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
    """
    gitleaks_plugin.set_options(
        no_git=False,
        config="/path/to/.gitleaks.toml",
        baseline_path="/path/to/baseline.json",
        redact=True,
        max_target_megabytes=50,
    )
    cmd = gitleaks_plugin._build_check_command(
        source_path="/path/to/source",
        report_path="/tmp/report.json",
    )

    assert_that(cmd).contains("gitleaks")
    assert_that(cmd).contains("detect")
    assert_that(cmd).does_not_contain("--no-git")
    assert_that(cmd).contains("--config")
    assert_that(cmd).contains("--baseline-path")
    assert_that(cmd).contains("--redact")
    assert_that(cmd).contains("--max-target-megabytes")
    assert_that(cmd).contains("--report-format")
    assert_that(cmd).contains(GITLEAKS_OUTPUT_FORMAT)
    assert_that(cmd).contains("--report-path")
