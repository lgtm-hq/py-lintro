"""Unit tests for shfmt plugin options."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.tools.shfmt.definition import (
    SHFMT_DEFAULT_TIMEOUT,
    ShfmtPlugin,
)

if TYPE_CHECKING:
    pass


# =============================================================================
# Tests for default options
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", SHFMT_DEFAULT_TIMEOUT),
        ("indent", None),
        ("binary_next_line", False),
        ("switch_case_indent", False),
        ("space_redirects", False),
        ("language_dialect", None),
        ("simplify", False),
    ],
    ids=[
        "timeout_equals_default",
        "indent_is_none",
        "binary_next_line_is_false",
        "switch_case_indent_is_false",
        "space_redirects_is_false",
        "language_dialect_is_none",
        "simplify_is_false",
    ],
)
def test_default_options_values(
    shfmt_plugin: ShfmtPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        shfmt_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# =============================================================================
# Tests for ShfmtPlugin.set_options method - valid options
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("indent", 0),
        ("indent", 2),
        ("indent", 4),
        ("binary_next_line", True),
        ("switch_case_indent", True),
        ("space_redirects", True),
        ("language_dialect", "bash"),
        ("language_dialect", "posix"),
        ("language_dialect", "mksh"),
        ("language_dialect", "bats"),
        ("simplify", True),
    ],
    ids=[
        "indent_0_tabs",
        "indent_2_spaces",
        "indent_4_spaces",
        "binary_next_line_true",
        "switch_case_indent_true",
        "space_redirects_true",
        "language_dialect_bash",
        "language_dialect_posix",
        "language_dialect_mksh",
        "language_dialect_bats",
        "simplify_true",
    ],
)
def test_set_options_valid(
    shfmt_plugin: ShfmtPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        option_name: The name of the option to set.
        option_value: The value to set for the option.
    """
    shfmt_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]
    assert_that(shfmt_plugin.options.get(option_name)).is_equal_to(option_value)


def test_set_options_language_dialect_case_insensitive(
    shfmt_plugin: ShfmtPlugin,
) -> None:
    """Set language_dialect with different case normalizes to lowercase.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    shfmt_plugin.set_options(language_dialect="BASH")
    assert_that(shfmt_plugin.options.get("language_dialect")).is_equal_to("bash")


# =============================================================================
# Tests for ShfmtPlugin.set_options method - invalid types
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("indent", "four", "indent must be an integer"),
        ("indent", 4.5, "indent must be an integer"),
        ("binary_next_line", "yes", "binary_next_line must be a boolean"),
        ("binary_next_line", 1, "binary_next_line must be a boolean"),
        ("switch_case_indent", "true", "switch_case_indent must be a boolean"),
        ("space_redirects", "no", "space_redirects must be a boolean"),
        ("language_dialect", 123, "language_dialect must be a string"),
        ("language_dialect", "invalid", "Invalid language_dialect"),
        ("language_dialect", "sh", "Invalid language_dialect"),
        ("simplify", "yes", "simplify must be a boolean"),
    ],
    ids=[
        "invalid_indent_string",
        "invalid_indent_float",
        "invalid_binary_next_line_string",
        "invalid_binary_next_line_int",
        "invalid_switch_case_indent_string",
        "invalid_space_redirects_string",
        "invalid_language_dialect_int",
        "invalid_language_dialect_value",
        "invalid_language_dialect_sh",
        "invalid_simplify_string",
    ],
)
def test_set_options_invalid_type(
    shfmt_plugin: ShfmtPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        option_name: The name of the option being tested.
        invalid_value: An invalid value for the option.
        error_match: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=error_match):
        shfmt_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]


# =============================================================================
# Tests for ShfmtPlugin._build_common_args method
# =============================================================================


def test_build_common_args_no_options(shfmt_plugin: ShfmtPlugin) -> None:
    """Build common args with no options set returns empty list.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    args = shfmt_plugin._build_common_args()
    assert_that(args).is_empty()


def test_build_common_args_with_indent(shfmt_plugin: ShfmtPlugin) -> None:
    """Build common args with indent option.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    shfmt_plugin.set_options(indent=4)
    args = shfmt_plugin._build_common_args()

    assert_that(args).contains("-i")
    indent_idx = args.index("-i")
    assert_that(args[indent_idx + 1]).is_equal_to("4")


def test_build_common_args_with_indent_zero(shfmt_plugin: ShfmtPlugin) -> None:
    """Build common args with indent=0 for tabs.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    shfmt_plugin.set_options(indent=0)
    args = shfmt_plugin._build_common_args()

    assert_that(args).contains("-i")
    indent_idx = args.index("-i")
    assert_that(args[indent_idx + 1]).is_equal_to("0")


def test_build_common_args_with_binary_next_line(shfmt_plugin: ShfmtPlugin) -> None:
    """Build common args with binary_next_line option.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    shfmt_plugin.set_options(binary_next_line=True)
    args = shfmt_plugin._build_common_args()

    assert_that(args).contains("-bn")


def test_build_common_args_with_switch_case_indent(shfmt_plugin: ShfmtPlugin) -> None:
    """Build common args with switch_case_indent option.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    shfmt_plugin.set_options(switch_case_indent=True)
    args = shfmt_plugin._build_common_args()

    assert_that(args).contains("-ci")


def test_build_common_args_with_space_redirects(shfmt_plugin: ShfmtPlugin) -> None:
    """Build common args with space_redirects option.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    shfmt_plugin.set_options(space_redirects=True)
    args = shfmt_plugin._build_common_args()

    assert_that(args).contains("-sr")


def test_build_common_args_with_language_dialect(shfmt_plugin: ShfmtPlugin) -> None:
    """Build common args with language_dialect option.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    shfmt_plugin.set_options(language_dialect="bash")
    args = shfmt_plugin._build_common_args()

    assert_that(args).contains("-ln")
    ln_idx = args.index("-ln")
    assert_that(args[ln_idx + 1]).is_equal_to("bash")


def test_build_common_args_with_simplify(shfmt_plugin: ShfmtPlugin) -> None:
    """Build common args with simplify option.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    shfmt_plugin.set_options(simplify=True)
    args = shfmt_plugin._build_common_args()

    assert_that(args).contains("-s")


def test_build_common_args_with_all_options(shfmt_plugin: ShfmtPlugin) -> None:
    """Build common args with all options set.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
    """
    shfmt_plugin.set_options(
        indent=2,
        binary_next_line=True,
        switch_case_indent=True,
        space_redirects=True,
        language_dialect="posix",
        simplify=True,
    )
    args = shfmt_plugin._build_common_args()

    assert_that(args).contains("-i")
    assert_that(args).contains("-bn")
    assert_that(args).contains("-ci")
    assert_that(args).contains("-sr")
    assert_that(args).contains("-ln")
    assert_that(args).contains("-s")

    # Verify values
    indent_idx = args.index("-i")
    assert_that(args[indent_idx + 1]).is_equal_to("2")

    ln_idx = args.index("-ln")
    assert_that(args[ln_idx + 1]).is_equal_to("posix")
