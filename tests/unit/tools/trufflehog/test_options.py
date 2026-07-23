"""Unit tests for trufflehog plugin options and command building."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.definitions.trufflehog import (
    TRUFFLEHOG_DEFAULT_TIMEOUT,
    TrufflehogPlugin,
)


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", TRUFFLEHOG_DEFAULT_TIMEOUT),
        ("no_verification", True),
        ("results", None),
        ("config", None),
        ("exclude_paths", None),
        ("concurrency", None),
    ],
    ids=[
        "timeout_default",
        "no_verification_true",
        "results_none",
        "config_none",
        "exclude_paths_none",
        "concurrency_none",
    ],
)
def test_default_options_values(
    trufflehog_plugin: TrufflehogPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options should have the expected values.

    Args:
        trufflehog_plugin: The plugin under test.
        option_name: Option to check.
        expected_value: Expected default value.
    """
    assert_that(
        trufflehog_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("no_verification", False),
        ("results", "verified,unverified"),
        ("config", "/path/to/config.yaml"),
        ("exclude_paths", "/path/to/exclude.txt"),
        ("concurrency", 4),
    ],
    ids=[
        "no_verification_false",
        "results_filter",
        "config_path",
        "exclude_paths_path",
        "concurrency_4",
    ],
)
def test_set_options_valid(
    trufflehog_plugin: TrufflehogPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Valid options should be accepted and stored.

    Args:
        trufflehog_plugin: The plugin under test.
        option_name: Option to set.
        option_value: Value to set.
    """
    trufflehog_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]
    assert_that(trufflehog_plugin.options.get(option_name)).is_equal_to(option_value)


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("no_verification", "yes", "no_verification must be a boolean"),
        ("results", 123, "results must be a string"),
        ("config", True, "config must be a string"),
        ("exclude_paths", 456, "exclude_paths must be a string"),
        ("concurrency", "many", "concurrency must be an integer"),
        ("concurrency", 0, "concurrency must be positive"),
        ("concurrency", -1, "concurrency must be positive"),
    ],
    ids=[
        "no_verification_string",
        "results_int",
        "config_bool",
        "exclude_paths_int",
        "concurrency_string",
        "concurrency_zero",
        "concurrency_negative",
    ],
)
def test_set_options_invalid(
    trufflehog_plugin: TrufflehogPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Invalid option types should raise ValueError.

    Args:
        trufflehog_plugin: The plugin under test.
        option_name: Option being tested.
        invalid_value: Invalid value.
        error_match: Expected error pattern.
    """
    with pytest.raises(ValueError, match=error_match):
        trufflehog_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]


def test_build_check_command_defaults(trufflehog_plugin: TrufflehogPlugin) -> None:
    """The default command should scan in filesystem mode without verification.

    Args:
        trufflehog_plugin: The plugin under test.
    """
    cmd = trufflehog_plugin._build_check_command(source_paths=["/abs/path"])

    assert_that(cmd).contains("trufflehog")
    assert_that(cmd).contains("filesystem")
    assert_that(cmd).contains("/abs/path")
    assert_that(cmd).contains("--json")
    assert_that(cmd).contains("--no-verification")
    # The self-updater must never run against the baked-in binary.
    assert_that(cmd).contains("--no-update")


def test_build_check_command_verification_enabled(
    trufflehog_plugin: TrufflehogPlugin,
) -> None:
    """Setting no_verification=False should drop the --no-verification flag.

    Args:
        trufflehog_plugin: The plugin under test.
    """
    trufflehog_plugin.set_options(no_verification=False)
    cmd = trufflehog_plugin._build_check_command(source_paths=["/abs/path"])

    assert_that(cmd).does_not_contain("--no-verification")


def test_build_check_command_optional_flags(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """Optional flags should be appended when set and paths exist.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory for on-disk option files.
    """
    config_file = tmp_path / "cfg.yaml"
    config_file.write_text("detectors: []\n")
    exclude_file = tmp_path / "exclude.txt"
    exclude_file.write_text("vendor/.*\n")

    trufflehog_plugin.set_options(
        results="verified,unverified",
        config=str(config_file),
        exclude_paths=str(exclude_file),
        concurrency=8,
    )
    cmd = trufflehog_plugin._build_check_command(source_paths=["/abs/path"])

    assert_that(cmd).contains("--results")
    assert_that(cmd).contains("verified,unverified")
    assert_that(cmd).contains("--config")
    assert_that(cmd).contains(str(config_file))
    assert_that(cmd).contains("--exclude-paths")
    assert_that(cmd).contains(str(exclude_file))
    assert_that(cmd).contains("--concurrency")
    assert_that(cmd).contains("8")


def test_build_check_command_fails_when_config_is_absent(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """A missing explicit config must fail instead of disabling detectors.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    missing_config = tmp_path / "missing-config.yaml"
    trufflehog_plugin.set_options(config=str(missing_config))

    with pytest.raises(ValueError, match="config file does not exist"):
        trufflehog_plugin._build_check_command(source_paths=["/abs/path"])


def test_build_check_command_skips_absent_exclude_paths(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """Absent CI-only exclude files must not be passed to TruffleHog.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    missing_exclude = tmp_path / "missing-exclude.txt"
    trufflehog_plugin.set_options(exclude_paths=str(missing_exclude))
    cmd = trufflehog_plugin._build_check_command(source_paths=["/abs/path"])

    assert_that(cmd).does_not_contain("--config")
    assert_that(cmd).does_not_contain("--exclude-paths")
    assert_that(cmd).does_not_contain(str(missing_exclude))


def test_build_check_command_multiple_paths(
    trufflehog_plugin: TrufflehogPlugin,
) -> None:
    """Multiple source paths should all be passed to the command.

    Args:
        trufflehog_plugin: The plugin under test.
    """
    cmd = trufflehog_plugin._build_check_command(
        source_paths=["/abs/a", "/abs/b"],
    )

    assert_that(cmd).contains("/abs/a")
    assert_that(cmd).contains("/abs/b")


def test_definition_metadata(trufflehog_plugin: TrufflehogPlugin) -> None:
    """The tool definition should mark trufflehog as a no-fix security tool.

    Args:
        trufflehog_plugin: The plugin under test.
    """
    definition = trufflehog_plugin.definition
    assert_that(definition.name).is_equal_to("trufflehog")
    assert_that(definition.can_fix).is_false()
    assert_that(definition.file_patterns).is_equal_to(["*"])
