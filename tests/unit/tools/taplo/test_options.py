"""Tests for TaploPlugin options configuration and command building."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.taplo.definition import (
    TAPLO_DEFAULT_TIMEOUT,
    TaploPlugin,
)

# Tests for TaploPlugin default options


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", TAPLO_DEFAULT_TIMEOUT),
        ("schema", None),
        ("aligned_arrays", None),
        ("aligned_entries", None),
        ("array_trailing_comma", None),
        ("indent_string", None),
        ("reorder_keys", None),
    ],
    ids=[
        "timeout_equals_default",
        "schema_is_none",
        "aligned_arrays_is_none",
        "aligned_entries_is_none",
        "array_trailing_comma_is_none",
        "indent_string_is_none",
        "reorder_keys_is_none",
    ],
)
def test_default_options_values(
    taplo_plugin: TaploPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        taplo_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# Tests for TaploPlugin.set_options method - valid options


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("schema", "/path/to/schema.json"),
        ("aligned_arrays", True),
        ("aligned_entries", True),
        ("array_trailing_comma", True),
        ("indent_string", "    "),
        ("indent_string", "\t"),
        ("reorder_keys", True),
        ("reorder_keys", False),
    ],
    ids=[
        "schema_path",
        "aligned_arrays_true",
        "aligned_entries_true",
        "array_trailing_comma_true",
        "indent_string_spaces",
        "indent_string_tab",
        "reorder_keys_true",
        "reorder_keys_false",
    ],
)
def test_set_options_valid(
    taplo_plugin: TaploPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        option_name: The name of the option to set.
        option_value: The value to set for the option.
    """
    taplo_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]
    assert_that(taplo_plugin.options.get(option_name)).is_equal_to(option_value)


# Tests for TaploPlugin.set_options method - invalid types


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("schema", 123, "schema must be a string"),
        ("schema", ["path"], "schema must be a string"),
        ("aligned_arrays", "yes", "aligned_arrays must be a boolean"),
        ("aligned_arrays", 1, "aligned_arrays must be a boolean"),
        ("aligned_entries", "true", "aligned_entries must be a boolean"),
        ("array_trailing_comma", 0, "array_trailing_comma must be a boolean"),
        ("indent_string", 4, "indent_string must be a string"),
        ("indent_string", True, "indent_string must be a string"),
        ("reorder_keys", "yes", "reorder_keys must be a boolean"),
    ],
    ids=[
        "invalid_schema_int",
        "invalid_schema_list",
        "invalid_aligned_arrays_str",
        "invalid_aligned_arrays_int",
        "invalid_aligned_entries_str",
        "invalid_array_trailing_comma_int",
        "invalid_indent_string_int",
        "invalid_indent_string_bool",
        "invalid_reorder_keys_str",
    ],
)
def test_set_options_invalid_type(
    taplo_plugin: TaploPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        option_name: The name of the option being tested.
        invalid_value: An invalid value for the option.
        error_match: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=error_match):
        taplo_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]


# Tests for TaploPlugin._build_format_args method


def test_build_format_args_no_options(taplo_plugin: TaploPlugin) -> None:
    """Build format args returns empty list when no options set.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    args = taplo_plugin._build_format_args()
    assert_that(args).is_empty()


def test_build_format_args_with_aligned_arrays(taplo_plugin: TaploPlugin) -> None:
    """Build format args includes aligned_arrays option.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    taplo_plugin.set_options(aligned_arrays=True)
    args = taplo_plugin._build_format_args()

    assert_that(args).contains("--option=aligned_arrays=true")


def test_build_format_args_with_aligned_entries(taplo_plugin: TaploPlugin) -> None:
    """Build format args includes aligned_entries option.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    taplo_plugin.set_options(aligned_entries=True)
    args = taplo_plugin._build_format_args()

    assert_that(args).contains("--option=aligned_entries=true")


def test_build_format_args_with_array_trailing_comma(
    taplo_plugin: TaploPlugin,
) -> None:
    """Build format args includes array_trailing_comma option.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    taplo_plugin.set_options(array_trailing_comma=True)
    args = taplo_plugin._build_format_args()

    assert_that(args).contains("--option=array_trailing_comma=true")


def test_build_format_args_with_indent_string(taplo_plugin: TaploPlugin) -> None:
    """Build format args includes indent_string option.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    taplo_plugin.set_options(indent_string="    ")
    args = taplo_plugin._build_format_args()

    assert_that(args).contains("--option=indent_string=    ")


def test_build_format_args_with_reorder_keys(taplo_plugin: TaploPlugin) -> None:
    """Build format args includes reorder_keys option.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    taplo_plugin.set_options(reorder_keys=True)
    args = taplo_plugin._build_format_args()

    assert_that(args).contains("--option=reorder_keys=true")


def test_build_format_args_with_all_options(taplo_plugin: TaploPlugin) -> None:
    """Build format args includes all formatting options.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    taplo_plugin.set_options(
        aligned_arrays=True,
        aligned_entries=True,
        array_trailing_comma=True,
        indent_string="\t",
        reorder_keys=True,
    )
    args = taplo_plugin._build_format_args()

    assert_that(args).contains("--option=aligned_arrays=true")
    assert_that(args).contains("--option=aligned_entries=true")
    assert_that(args).contains("--option=array_trailing_comma=true")
    assert_that(args).contains("--option=indent_string=\t")
    assert_that(args).contains("--option=reorder_keys=true")
    assert_that(args).is_length(5)


# Tests for TaploPlugin._build_lint_args method


def test_build_lint_args_no_options(taplo_plugin: TaploPlugin) -> None:
    """Build lint args returns empty list when no options set.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    args = taplo_plugin._build_lint_args()
    assert_that(args).is_empty()


def test_build_lint_args_with_schema(taplo_plugin: TaploPlugin) -> None:
    """Build lint args includes schema option.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    taplo_plugin.set_options(schema="/path/to/schema.json")
    args = taplo_plugin._build_lint_args()

    assert_that(args).contains("--schema")
    schema_idx = args.index("--schema")
    assert_that(args[schema_idx + 1]).is_equal_to("/path/to/schema.json")


def test_build_lint_args_with_url_schema(taplo_plugin: TaploPlugin) -> None:
    """Build lint args includes schema URL.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
    """
    schema_url = "https://json.schemastore.org/pyproject.json"
    taplo_plugin.set_options(schema=schema_url)
    args = taplo_plugin._build_lint_args()

    assert_that(args).contains("--schema")
    schema_idx = args.index("--schema")
    assert_that(args[schema_idx + 1]).is_equal_to(schema_url)
