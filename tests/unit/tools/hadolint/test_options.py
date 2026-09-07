"""Unit tests for hadolint plugin options and command building."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.hadolint.definition import (
    HADOLINT_DEFAULT_FAILURE_THRESHOLD,
    HADOLINT_DEFAULT_FORMAT,
    HADOLINT_DEFAULT_NO_COLOR,
    HADOLINT_DEFAULT_TIMEOUT,
    HadolintPlugin,
)


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", HADOLINT_DEFAULT_TIMEOUT),
        ("format", HADOLINT_DEFAULT_FORMAT),
        ("failure_threshold", HADOLINT_DEFAULT_FAILURE_THRESHOLD),
        ("no_color", HADOLINT_DEFAULT_NO_COLOR),
    ],
    ids=[
        "timeout_equals_default",
        "format_equals_default",
        "failure_threshold_equals_default",
        "no_color_equals_default",
    ],
)
def test_default_options_values(
    hadolint_plugin: HadolintPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(hadolint_plugin.definition.default_options).contains_key(option_name)
    assert_that(hadolint_plugin.definition.default_options[option_name]).is_equal_to(
        expected_value,
    )


# =============================================================================
# Tests for HadolintPlugin.set_options method
# =============================================================================


def test_set_options_format(hadolint_plugin: HadolintPlugin) -> None:
    """Set format option.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(format="json")
    assert_that(hadolint_plugin.options.get("format")).is_equal_to("json")


def test_set_options_failure_threshold(hadolint_plugin: HadolintPlugin) -> None:
    """Set failure_threshold option.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(failure_threshold="warning")
    assert_that(hadolint_plugin.options.get("failure_threshold")).is_equal_to("warning")


def test_set_options_ignore(hadolint_plugin: HadolintPlugin) -> None:
    """Set ignore option.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    rules = ["DL3006", "SC2086"]
    hadolint_plugin.set_options(ignore=rules)
    assert_that(hadolint_plugin.options.get("ignore")).is_equal_to(rules)


def test_set_options_trusted_registries(hadolint_plugin: HadolintPlugin) -> None:
    """Set trusted_registries option.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    registries = ["docker.io", "gcr.io"]
    hadolint_plugin.set_options(trusted_registries=registries)
    assert_that(hadolint_plugin.options.get("trusted_registries")).is_equal_to(
        registries,
    )


def test_set_options_require_labels(hadolint_plugin: HadolintPlugin) -> None:
    """Set require_labels option.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    labels = ["maintainer:text", "version:semver"]
    hadolint_plugin.set_options(require_labels=labels)
    assert_that(hadolint_plugin.options.get("require_labels")).is_equal_to(labels)


def test_set_options_strict_labels(hadolint_plugin: HadolintPlugin) -> None:
    """Set strict_labels option.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(strict_labels=True)
    assert_that(hadolint_plugin.options.get("strict_labels")).is_true()


def test_set_options_no_fail(hadolint_plugin: HadolintPlugin) -> None:
    """Set no_fail option.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(no_fail=True)
    assert_that(hadolint_plugin.options.get("no_fail")).is_true()


def test_set_options_no_color(hadolint_plugin: HadolintPlugin) -> None:
    """Set no_color option.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(no_color=False)
    assert_that(hadolint_plugin.options.get("no_color")).is_false()


def test_set_options_no_options(hadolint_plugin: HadolintPlugin) -> None:
    """Handle no options set.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options()
    # Should not raise


def test_set_options_invalid_ignore_type(hadolint_plugin: HadolintPlugin) -> None:
    """Raise ValueError for invalid ignore type.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    with pytest.raises(ValueError, match="ignore must be a list"):
        hadolint_plugin.set_options(ignore="DL3006")  # type: ignore[arg-type]


def test_set_options_invalid_strict_labels_type(
    hadolint_plugin: HadolintPlugin,
) -> None:
    """Raise ValueError for invalid strict_labels type.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    with pytest.raises(ValueError, match="strict_labels must be a boolean"):
        hadolint_plugin.set_options(strict_labels="yes")  # type: ignore[arg-type]


# =============================================================================
# Tests for HadolintPlugin._build_command method
# =============================================================================


def test_build_command_default(hadolint_plugin: HadolintPlugin) -> None:
    """Build command with default options.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    cmd = hadolint_plugin._build_command()

    assert_that(cmd).contains("hadolint")
    assert_that(cmd).contains("--format", "tty")
    assert_that(cmd).contains("--failure-threshold", "info")
    assert_that(cmd).contains("--no-color")


def test_build_command_with_format(hadolint_plugin: HadolintPlugin) -> None:
    """Build command with custom format.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(format="json")
    cmd = hadolint_plugin._build_command()

    assert_that("--format" in cmd).is_true()
    idx = cmd.index("--format")
    assert_that(cmd[idx + 1]).is_equal_to("json")


def test_build_command_with_failure_threshold(hadolint_plugin: HadolintPlugin) -> None:
    """Build command with custom failure threshold.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(failure_threshold="error")
    cmd = hadolint_plugin._build_command()

    assert_that("--failure-threshold" in cmd).is_true()
    idx = cmd.index("--failure-threshold")
    assert_that(cmd[idx + 1]).is_equal_to("error")


def test_build_command_with_ignore(hadolint_plugin: HadolintPlugin) -> None:
    """Build command with ignore rules.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(ignore=["DL3006", "SC2086"])
    cmd = hadolint_plugin._build_command()

    assert_that(cmd.count("--ignore")).is_equal_to(2)
    assert_that(cmd).contains("DL3006")
    assert_that(cmd).contains("SC2086")


def test_build_command_with_trusted_registries(
    hadolint_plugin: HadolintPlugin,
) -> None:
    """Build command with trusted registries.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(trusted_registries=["docker.io", "gcr.io"])
    cmd = hadolint_plugin._build_command()

    assert_that(cmd.count("--trusted-registry")).is_equal_to(2)
    assert_that(cmd).contains("docker.io")
    assert_that(cmd).contains("gcr.io")


def test_build_command_with_require_labels(hadolint_plugin: HadolintPlugin) -> None:
    """Build command with required labels.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(require_labels=["maintainer:text"])
    cmd = hadolint_plugin._build_command()

    assert_that(cmd).contains("--require-label")
    assert_that(cmd).contains("maintainer:text")


def test_build_command_with_strict_labels(hadolint_plugin: HadolintPlugin) -> None:
    """Build command with strict labels.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(strict_labels=True)
    cmd = hadolint_plugin._build_command()

    assert_that(cmd).contains("--strict-labels")


def test_build_command_with_no_fail(hadolint_plugin: HadolintPlugin) -> None:
    """Build command with no-fail option.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(no_fail=True)
    cmd = hadolint_plugin._build_command()

    assert_that(cmd).contains("--no-fail")


def test_build_command_without_no_color(hadolint_plugin: HadolintPlugin) -> None:
    """Build command without no-color when disabled.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
    """
    hadolint_plugin.set_options(no_color=False)
    cmd = hadolint_plugin._build_command()

    assert_that("--no-color" in cmd).is_false()
