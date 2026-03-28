"""Tests for OxfmtPlugin default options."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.oxfmt import (
    OXFMT_DEFAULT_PRIORITY,
    OXFMT_DEFAULT_TIMEOUT,
    OxfmtPlugin,
)

if TYPE_CHECKING:
    pass


# =============================================================================
# Tests for ToolDefinition attributes
# =============================================================================


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "oxfmt"),
        (
            "description",
            "Fast JavaScript/TypeScript formatter (30x faster than Prettier)",
        ),
        ("can_fix", True),
        ("tool_type", ToolType.FORMATTER),
        ("priority", OXFMT_DEFAULT_PRIORITY),
        ("default_timeout", OXFMT_DEFAULT_TIMEOUT),
        ("min_version", "0.42.0"),
    ],
    ids=[
        "name_equals_oxfmt",
        "description_is_set",
        "can_fix_is_true",
        "tool_type_is_formatter",
        "priority_equals_80",
        "default_timeout_equals_30",
        "min_version_is_0.42.0",
    ],
)
def test_definition_attributes(
    oxfmt_plugin: OxfmtPlugin,
    attr: str,
    expected: object,
) -> None:
    """Definition attributes have correct values.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
        attr: The attribute name to check.
        expected: The expected value for the attribute.
    """
    assert_that(getattr(oxfmt_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(oxfmt_plugin: OxfmtPlugin) -> None:
    """Definition includes JavaScript/TypeScript/Vue file patterns.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    patterns = oxfmt_plugin.definition.file_patterns
    assert_that(patterns).contains("*.js")
    assert_that(patterns).contains("*.ts")
    assert_that(patterns).contains("*.jsx")
    assert_that(patterns).contains("*.tsx")
    assert_that(patterns).contains("*.vue")


def test_definition_native_configs(oxfmt_plugin: OxfmtPlugin) -> None:
    """Definition includes native config files.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    configs = oxfmt_plugin.definition.native_configs
    assert_that(configs).contains(".oxfmtrc.json")
    assert_that(configs).contains(".oxfmtrc.jsonc")


def test_definition_version_command(oxfmt_plugin: OxfmtPlugin) -> None:
    """Definition has a version command.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    assert_that(oxfmt_plugin.definition.version_command).is_not_none()
    assert_that(oxfmt_plugin.definition.version_command).contains("oxfmt")
    assert_that(oxfmt_plugin.definition.version_command).contains("--version")


def test_definition_conflicts_with(oxfmt_plugin: OxfmtPlugin) -> None:
    """Definition specifies conflicting tools.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    conflicts = oxfmt_plugin.definition.conflicts_with
    assert_that(conflicts).is_empty()


# =============================================================================
# Tests for default options
# =============================================================================


def test_default_options_timeout(oxfmt_plugin: OxfmtPlugin) -> None:
    """Default timeout option has correct value.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
    """
    assert_that(
        oxfmt_plugin.definition.default_options["timeout"],
    ).is_equal_to(OXFMT_DEFAULT_TIMEOUT)


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("verbose_fix_output", False),
    ],
    ids=[
        "verbose_fix_output_is_false",
    ],
)
def test_default_options_values(
    oxfmt_plugin: OxfmtPlugin,
    option_name: str,
    expected_value: Any,
) -> None:
    """Default options have correct values.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        oxfmt_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)
