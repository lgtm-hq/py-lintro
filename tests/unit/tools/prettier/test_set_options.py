"""Tests for PrettierPlugin.set_options method."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.tools.prettier.definition import PrettierPlugin


# Tests for valid options


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("verbose_fix_output", True),
        ("verbose_fix_output", False),
        ("line_length", 80),
        ("line_length", 120),
    ],
    ids=[
        "verbose_fix_output_true",
        "verbose_fix_output_false",
        "line_length_80",
        "line_length_120",
    ],
)
def test_set_options_valid(
    prettier_plugin: PrettierPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        prettier_plugin: The prettier plugin instance to test.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    prettier_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]
    assert_that(prettier_plugin.options.get(option_name)).is_equal_to(option_value)


# Tests for invalid types


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("verbose_fix_output", "yes", "verbose_fix_output must be a boolean"),
        ("verbose_fix_output", 1, "verbose_fix_output must be a boolean"),
        ("line_length", "eighty", "line_length must be an integer"),
        ("line_length", 0, "line_length must be positive"),
        ("line_length", -10, "line_length must be positive"),
    ],
    ids=[
        "invalid_verbose_fix_output_string",
        "invalid_verbose_fix_output_int",
        "invalid_line_length_string",
        "invalid_line_length_zero",
        "invalid_line_length_negative",
    ],
)
def test_set_options_invalid_type(
    prettier_plugin: PrettierPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        prettier_plugin: The prettier plugin instance to test.
        option_name: Name of the option being tested.
        invalid_value: Invalid value that should cause an error.
        error_match: Expected error message substring.
    """
    with pytest.raises(ValueError, match=error_match):
        prettier_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]
