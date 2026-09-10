"""Unit tests for Semgrep plugin options."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.semgrep.definition import (
    SEMGREP_DEFAULT_CONFIG,
    SEMGREP_DEFAULT_TIMEOUT,
    SemgrepPlugin,
)

# =============================================================================
# Tests for SemgrepPlugin default options
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", SEMGREP_DEFAULT_TIMEOUT),
        ("config", SEMGREP_DEFAULT_CONFIG),
        ("exclude", None),
        ("include", None),
        ("severity", None),
        ("timeout_threshold", None),
        ("jobs", None),
        ("verbose", False),
        ("quiet", False),
    ],
    ids=[
        "timeout_equals_default",
        "config_equals_auto",
        "exclude_is_none",
        "include_is_none",
        "severity_is_none",
        "timeout_threshold_is_none",
        "jobs_is_none",
        "verbose_is_false",
        "quiet_is_false",
    ],
)
def test_default_options_values(
    semgrep_plugin: SemgrepPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        semgrep_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# =============================================================================
# Tests for SemgrepPlugin.set_options method - valid options
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("config", "auto"),
        ("config", "p/python"),
        ("config", "p/javascript"),
        ("config", "/path/to/rules.yaml"),
        ("exclude", ["test_*.py", "vendor/*"]),
        ("include", ["src/*.py", "lib/*.py"]),
        ("severity", "INFO"),
        ("severity", "WARNING"),
        ("severity", "ERROR"),
        ("jobs", 4),
        ("jobs", 1),
        ("timeout_threshold", 30),
        ("timeout_threshold", 0),
        ("verbose", True),
        ("quiet", True),
    ],
    ids=[
        "config_auto",
        "config_python_ruleset",
        "config_javascript_ruleset",
        "config_custom_path",
        "exclude_patterns",
        "include_patterns",
        "severity_info",
        "severity_warning",
        "severity_error",
        "jobs_4",
        "jobs_1",
        "timeout_threshold_30",
        "timeout_threshold_0",
        "verbose_true",
        "quiet_true",
    ],
)
def test_set_options_valid(
    semgrep_plugin: SemgrepPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        option_name: The name of the option to set.
        option_value: The value to set for the option.
    """
    semgrep_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]

    # Severity is normalized to uppercase
    expected: object
    if option_name == "severity" and isinstance(option_value, str):
        expected = option_value.upper()
    else:
        expected = option_value

    assert_that(semgrep_plugin.options.get(option_name)).is_equal_to(expected)


def test_set_options_severity_lowercase(semgrep_plugin: SemgrepPlugin) -> None:
    """Set severity option with lowercase value normalizes to uppercase.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(severity="info")
    assert_that(semgrep_plugin.options.get("severity")).is_equal_to("INFO")


# =============================================================================
# Tests for SemgrepPlugin.set_options method - invalid types
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("severity", "CRITICAL", "Invalid Semgrep severity"),
        ("severity", "invalid", "Invalid Semgrep severity"),
        ("jobs", 0, "jobs must be a positive integer"),
        ("jobs", -1, "jobs must be a positive integer"),
        ("jobs", "four", "jobs must be a positive integer"),
        ("timeout_threshold", -1, "timeout_threshold must be a non-negative integer"),
        (
            "timeout_threshold",
            "slow",
            "timeout_threshold must be a non-negative integer",
        ),
        ("exclude", "*.py", "exclude must be a list"),
        ("include", "*.py", "include must be a list"),
        ("config", 123, "config must be a string"),
    ],
    ids=[
        "invalid_severity_critical",
        "invalid_severity_unknown",
        "invalid_jobs_zero",
        "invalid_jobs_negative",
        "invalid_jobs_type",
        "invalid_timeout_threshold_negative",
        "invalid_timeout_threshold_type",
        "invalid_exclude_type",
        "invalid_include_type",
        "invalid_config_type",
    ],
)
def test_set_options_invalid_type(
    semgrep_plugin: SemgrepPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        option_name: The name of the option being tested.
        invalid_value: An invalid value for the option.
        error_match: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=error_match):
        semgrep_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]


# =============================================================================
# Tests for SemgrepPlugin._build_check_command method
# =============================================================================


def test_build_check_command_basic(semgrep_plugin: SemgrepPlugin) -> None:
    """Build basic command with default options.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    cmd = semgrep_plugin._build_check_command(files=["src/"])

    assert_that(cmd).contains("semgrep")
    assert_that(cmd).contains("scan")
    assert_that(cmd).contains("--json")
    assert_that(cmd).contains("--config")
    # Default config is "auto"
    config_idx = cmd.index("--config")
    assert_that(cmd[config_idx + 1]).is_equal_to("auto")
    assert_that(cmd).contains("src/")


def test_build_check_command_with_config(semgrep_plugin: SemgrepPlugin) -> None:
    """Build command with custom config option.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(config="p/python")
    cmd = semgrep_plugin._build_check_command(files=["app.py"])

    assert_that(cmd).contains("--config")
    config_idx = cmd.index("--config")
    assert_that(cmd[config_idx + 1]).is_equal_to("p/python")


def test_build_check_command_with_exclude(semgrep_plugin: SemgrepPlugin) -> None:
    """Build command with exclude patterns.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(exclude=["tests/*", "vendor/*"])
    cmd = semgrep_plugin._build_check_command(files=["src/"])

    # Each exclude pattern should have its own --exclude flag
    exclude_indices = [i for i, x in enumerate(cmd) if x == "--exclude"]
    assert_that(exclude_indices).is_length(2)
    assert_that(cmd[exclude_indices[0] + 1]).is_equal_to("tests/*")
    assert_that(cmd[exclude_indices[1] + 1]).is_equal_to("vendor/*")


def test_build_check_command_with_include(semgrep_plugin: SemgrepPlugin) -> None:
    """Build command with include patterns.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(include=["*.py", "*.js"])
    cmd = semgrep_plugin._build_check_command(files=["src/"])

    # Each include pattern should have its own --include flag
    include_indices = [i for i, x in enumerate(cmd) if x == "--include"]
    assert_that(include_indices).is_length(2)
    assert_that(cmd[include_indices[0] + 1]).is_equal_to("*.py")
    assert_that(cmd[include_indices[1] + 1]).is_equal_to("*.js")


def test_build_check_command_with_severity(semgrep_plugin: SemgrepPlugin) -> None:
    """Build command with severity filter.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(severity="ERROR")
    cmd = semgrep_plugin._build_check_command(files=["src/"])

    assert_that(cmd).contains("--severity")
    severity_idx = cmd.index("--severity")
    assert_that(cmd[severity_idx + 1]).is_equal_to("ERROR")


def test_build_check_command_with_jobs(semgrep_plugin: SemgrepPlugin) -> None:
    """Build command with jobs option.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(jobs=4)
    cmd = semgrep_plugin._build_check_command(files=["src/"])

    assert_that(cmd).contains("--jobs")
    jobs_idx = cmd.index("--jobs")
    assert_that(cmd[jobs_idx + 1]).is_equal_to("4")


def test_build_check_command_with_timeout_threshold(
    semgrep_plugin: SemgrepPlugin,
) -> None:
    """Build command with timeout_threshold option.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(timeout_threshold=30)
    cmd = semgrep_plugin._build_check_command(files=["src/"])

    assert_that(cmd).contains("--timeout")
    timeout_idx = cmd.index("--timeout")
    assert_that(cmd[timeout_idx + 1]).is_equal_to("30")


def test_build_check_command_with_verbose(semgrep_plugin: SemgrepPlugin) -> None:
    """Build command with verbose flag.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(verbose=True)
    cmd = semgrep_plugin._build_check_command(files=["src/"])

    assert_that(cmd).contains("--verbose")


def test_build_check_command_with_quiet(semgrep_plugin: SemgrepPlugin) -> None:
    """Build command with quiet flag.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(quiet=True)
    cmd = semgrep_plugin._build_check_command(files=["src/"])

    assert_that(cmd).contains("--quiet")


def test_build_check_command_with_all_options(semgrep_plugin: SemgrepPlugin) -> None:
    """Build command with all options set.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    semgrep_plugin.set_options(
        config="p/security-audit",
        exclude=["tests/*"],
        include=["*.py"],
        severity="WARNING",
        timeout_threshold=60,
        jobs=8,
        verbose=True,
    )
    cmd = semgrep_plugin._build_check_command(files=["src/", "lib/"])

    assert_that(cmd).contains("--config")
    assert_that(cmd).contains("--exclude")
    assert_that(cmd).contains("--include")
    assert_that(cmd).contains("--severity")
    assert_that(cmd).contains("--timeout")
    assert_that(cmd).contains("--jobs")
    assert_that(cmd).contains("--verbose")
    assert_that(cmd).contains("src/")
    assert_that(cmd).contains("lib/")
