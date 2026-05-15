"""Unit tests for rustfmt plugin options and definition."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.rustfmt import (
    RUSTFMT_DEFAULT_PRIORITY,
    RUSTFMT_DEFAULT_TIMEOUT,
    RustfmtPlugin,
)

if TYPE_CHECKING:
    pass


# =============================================================================
# Tests for ToolDefinition attributes
# =============================================================================


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "rustfmt"),
        ("description", "Rust's official code formatter"),
        ("can_fix", True),
        ("tool_type", ToolType.FORMATTER),
        ("priority", RUSTFMT_DEFAULT_PRIORITY),
        ("default_timeout", RUSTFMT_DEFAULT_TIMEOUT),
    ],
    ids=[
        "name_equals_rustfmt",
        "description_is_set",
        "can_fix_is_true",
        "tool_type_is_formatter",
        "priority_equals_80",
        "default_timeout_equals_60",
    ],
)
def test_definition_attributes(
    rustfmt_plugin: RustfmtPlugin,
    attr: str,
    expected: object,
) -> None:
    """Definition attributes have correct values.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        attr: The attribute name to check.
        expected: The expected value for the attribute.
    """
    assert_that(getattr(rustfmt_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(rustfmt_plugin: RustfmtPlugin) -> None:
    """Definition includes Rust file patterns.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
    """
    patterns = rustfmt_plugin.definition.file_patterns
    assert_that(patterns).contains("*.rs")


def test_definition_native_configs(rustfmt_plugin: RustfmtPlugin) -> None:
    """Definition includes native config files.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
    """
    configs = rustfmt_plugin.definition.native_configs
    assert_that(configs).contains("rustfmt.toml")
    assert_that(configs).contains(".rustfmt.toml")


def test_definition_version_command(rustfmt_plugin: RustfmtPlugin) -> None:
    """Definition has a version command.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
    """
    assert_that(rustfmt_plugin.definition.version_command).is_not_none()
    assert_that(rustfmt_plugin.definition.version_command).contains("rustfmt")
    assert_that(rustfmt_plugin.definition.version_command).contains("--version")


# =============================================================================
# Tests for default options
# =============================================================================


def test_default_options_timeout(rustfmt_plugin: RustfmtPlugin) -> None:
    """Default timeout option has correct value.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
    """
    assert_that(
        rustfmt_plugin.definition.default_options["timeout"],
    ).is_equal_to(RUSTFMT_DEFAULT_TIMEOUT)


# =============================================================================
# Tests for RustfmtPlugin.set_options method - valid options
# =============================================================================


@pytest.mark.parametrize(
    ("timeout_value",),
    [
        (30,),
        (60,),
        (120,),
        (300,),
    ],
    ids=[
        "timeout_30",
        "timeout_60",
        "timeout_120",
        "timeout_300",
    ],
)
def test_set_options_valid_timeout(
    rustfmt_plugin: RustfmtPlugin,
    timeout_value: int,
) -> None:
    """Set valid timeout options correctly.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        timeout_value: The timeout value to set.
    """
    rustfmt_plugin.set_options(timeout=timeout_value)
    assert_that(rustfmt_plugin.options.get("timeout")).is_equal_to(timeout_value)


# =============================================================================
# Tests for RustfmtPlugin.set_options method - invalid options
# =============================================================================


@pytest.mark.parametrize(
    ("timeout_value", "error_match"),
    [
        (-1, "must be positive"),
        (0, "must be positive"),
        (-100, "must be positive"),
    ],
    ids=[
        "negative_timeout",
        "zero_timeout",
        "large_negative_timeout",
    ],
)
def test_set_options_invalid_timeout(
    rustfmt_plugin: RustfmtPlugin,
    timeout_value: int,
    error_match: str,
) -> None:
    """Raise ValueError for invalid timeout values.

    Args:
        rustfmt_plugin: The RustfmtPlugin instance to test.
        timeout_value: The invalid timeout value to test.
        error_match: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=error_match):
        rustfmt_plugin.set_options(timeout=timeout_value)
