"""Unit tests for option_validators module."""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.tools.core.option_validators import (
    OptionSchema,
    filter_none_options,
    normalize_str_or_list,
    validate_bool,
    validate_int,
    validate_list,
    validate_option_types,
    validate_positive_int,
    validate_str,
)

# =============================================================================
# validate_bool tests
# =============================================================================


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(True, id="true"),
        pytest.param(False, id="false"),
        pytest.param(None, id="none"),
    ],
)
def test_validate_bool_accepts_valid_values(value: bool | None) -> None:
    """Accept True, False, and None values.

    Args:
        value: The boolean value to validate.
    """
    # Should not raise - test passes if no exception
    validate_bool(value, "test")


@pytest.mark.parametrize(
    ("value", "description"),
    [
        pytest.param("true", "string", id="string"),
        pytest.param(1, "integer", id="int"),
    ],
)
def test_validate_bool_rejects_invalid_values(value: Any, description: str) -> None:
    """Reject non-boolean values like {description}.

    Args:
        value: The invalid value to test.
        description: Description of the test case.
    """
    with pytest.raises(ValueError, match="must be a boolean"):
        validate_bool(value, "test")


# =============================================================================
# validate_str tests
# =============================================================================


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("hello", id="non_empty_string"),
        pytest.param("", id="empty_string"),
        pytest.param(None, id="none"),
    ],
)
def test_validate_str_accepts_valid_values(value: str | None) -> None:
    """Accept string and None values.

    Args:
        value: The string value to validate.
    """
    # Should not raise - test passes if no exception
    validate_str(value, "test")


@pytest.mark.parametrize(
    ("value", "description"),
    [
        pytest.param(123, "integer", id="int"),
        pytest.param(["a", "b"], "list", id="list"),
    ],
)
def test_validate_str_rejects_invalid_values(value: Any, description: str) -> None:
    """Reject non-string values like {description}.

    Args:
        value: The invalid value to test.
        description: Description of the test case.
    """
    with pytest.raises(ValueError, match="must be a string"):
        validate_str(value, "test")


# =============================================================================
# validate_int tests
# =============================================================================


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(42, id="positive_int"),
        pytest.param(0, id="zero"),
        pytest.param(-5, id="negative_int"),
        pytest.param(None, id="none"),
    ],
)
def test_validate_int_accepts_valid_values(value: int | None) -> None:
    """Accept integer and None values.

    Args:
        value: The integer value to validate.
    """
    # Should not raise - test passes if no exception
    validate_int(value, "test")


@pytest.mark.parametrize(
    ("value", "description"),
    [
        pytest.param("42", "string", id="string"),
        pytest.param(3.14, "float", id="float"),
    ],
)
def test_validate_int_rejects_invalid_values(value: Any, description: str) -> None:
    """Reject non-integer values like {description}.

    Args:
        value: The invalid value to test.
        description: Description of the test case.
    """
    with pytest.raises(ValueError, match="must be an integer"):
        validate_int(value, "test")


def test_validate_int_with_min_value() -> None:
    """Accept values at or above min_value."""
    validate_int(5, "test", min_value=5)  # Equal to min
    validate_int(10, "test", min_value=5)  # Above min


def test_validate_int_rejects_below_min_value() -> None:
    """Reject values below min_value."""
    with pytest.raises(ValueError, match="must be at least 5"):
        validate_int(4, "test", min_value=5)


def test_validate_int_with_max_value() -> None:
    """Accept values at or below max_value."""
    validate_int(10, "test", max_value=10)  # Equal to max
    validate_int(5, "test", max_value=10)  # Below max


def test_validate_int_rejects_above_max_value() -> None:
    """Reject values above max_value."""
    with pytest.raises(ValueError, match="must be at most 10"):
        validate_int(11, "test", max_value=10)


def test_validate_int_with_range() -> None:
    """Accept values within min and max range."""
    validate_int(5, "test", min_value=1, max_value=10)
    validate_int(1, "test", min_value=1, max_value=10)
    validate_int(10, "test", min_value=1, max_value=10)


def test_validate_int_none_with_range() -> None:
    """Accept None even when range is specified."""
    validate_int(None, "test", min_value=1, max_value=10)


# =============================================================================
# validate_positive_int tests
# =============================================================================


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(42, id="positive_int"),
        pytest.param(None, id="none"),
    ],
)
def test_validate_positive_int_accepts_valid_values(value: int | None) -> None:
    """Accept positive integer and None values.

    Args:
        value: The positive integer value to validate.
    """
    # Should not raise - test passes if no exception
    validate_positive_int(value, "test")


@pytest.mark.parametrize(
    ("value", "match_pattern"),
    [
        pytest.param(0, "must be positive", id="zero"),
        pytest.param(-5, "must be positive", id="negative"),
        pytest.param("42", "must be an integer", id="string"),
    ],
)
def test_validate_positive_int_rejects_invalid_values(
    value: Any,
    match_pattern: str,
) -> None:
    """Reject zero, negative, and non-integer values.

    Args:
        value: The invalid value to test.
        match_pattern: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=match_pattern):
        validate_positive_int(value, "test")


# =============================================================================
# validate_list tests
# =============================================================================


@pytest.mark.parametrize(
    "value",
    [
        pytest.param([1, 2, 3], id="non_empty_list"),
        pytest.param([], id="empty_list"),
        pytest.param(None, id="none"),
    ],
)
def test_validate_list_accepts_valid_values(value: list[Any] | None) -> None:
    """Accept list and None values.

    Args:
        value: The list value to validate.
    """
    # Should not raise - test passes if no exception
    validate_list(value, "test")


@pytest.mark.parametrize(
    ("value", "description"),
    [
        pytest.param("a,b,c", "string", id="string"),
        pytest.param((1, 2, 3), "tuple", id="tuple"),
    ],
)
def test_validate_list_rejects_invalid_values(value: Any, description: str) -> None:
    """Reject non-list values like {description}.

    Args:
        value: The invalid value to test.
        description: Description of the test case.
    """
    with pytest.raises(ValueError, match="must be a list"):
        validate_list(value, "test")


# =============================================================================
# normalize_str_or_list tests
# =============================================================================


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none_returns_none"),
        pytest.param("hello", ["hello"], id="string_returns_list"),
        pytest.param(["a", "b"], ["a", "b"], id="list_returns_list"),
    ],
)
def test_normalize_str_or_list_valid_values(
    value: str | list[str] | None,
    expected: list[str] | None,
) -> None:
    """Normalize string to list and pass through list/None.

    Args:
        value: The input value to normalize.
        expected: The expected normalized result.
    """
    result = normalize_str_or_list(value, "test")
    if expected is None:
        assert_that(result).is_none()
    else:
        assert_that(result).is_equal_to(expected)


@pytest.mark.parametrize(
    ("value", "description"),
    [
        pytest.param(123, "integer", id="int"),
        pytest.param({"key": "value"}, "dict", id="dict"),
    ],
)
def test_normalize_str_or_list_rejects_invalid_values(
    value: Any,
    description: str,
) -> None:
    """Reject non-string/list values like {description}.

    Args:
        value: The invalid value to test.
        description: Description of the test case.
    """
    with pytest.raises(ValueError, match="must be a string or list"):
        normalize_str_or_list(value, "test")


# =============================================================================
# filter_none_options tests
# =============================================================================


def test_filter_none_options_filters_none_values() -> None:
    """Filter out None values."""
    result = filter_none_options(a=1, b=None, c="hello", d=None)
    assert_that(result).is_equal_to({"a": 1, "c": "hello"})


def test_filter_none_options_empty_input() -> None:
    """Return empty dict for empty input."""
    result = filter_none_options()
    assert_that(result).is_empty()


def test_filter_none_options_all_none() -> None:
    """Return empty dict when all values are None."""
    result = filter_none_options(a=None, b=None)
    assert_that(result).is_empty()


def test_filter_none_options_no_none() -> None:
    """Return all values when none are None."""
    result = filter_none_options(a=1, b=2, c=3)
    assert_that(result).is_equal_to({"a": 1, "b": 2, "c": 3})


def test_filter_none_options_preserves_falsy_values() -> None:
    """Preserve False, 0, empty string (not None)."""
    result = filter_none_options(a=False, b=0, c="", d=None)
    assert_that(result).is_equal_to({"a": False, "b": 0, "c": ""})


# =============================================================================
# validate_option_types tests
# =============================================================================

_SCHEMA: OptionSchema = {
    "strict": bool,
    "python_version": str,
    "config_file": (str, "string path"),
    "jobs": int,
}


def test_validate_option_types_accepts_valid_values() -> None:
    """Accept values that match their declared types."""
    validate_option_types(
        {
            "strict": True,
            "python_version": "3.11",
            "config_file": "mypy.ini",
            "jobs": 4,
        },
        _SCHEMA,
    )


def test_validate_option_types_skips_none_values() -> None:
    """Skip options whose value is None."""
    validate_option_types(
        {"strict": None, "python_version": None},
        _SCHEMA,
    )


def test_validate_option_types_skips_absent_options() -> None:
    """Accept an empty mapping without raising."""
    validate_option_types({}, _SCHEMA)


def test_validate_option_types_ignores_options_outside_schema() -> None:
    """Pass through options not declared in the schema without raising."""
    validate_option_types(
        {"strict": True, "unknown_option": object()},
        _SCHEMA,
    )


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        pytest.param("strict", "yes", "strict must be a boolean", id="bool"),
        pytest.param(
            "python_version",
            310,
            "python_version must be a string",
            id="str",
        ),
        pytest.param(
            "config_file",
            123,
            "config_file must be a string path",
            id="str-custom-label",
        ),
        pytest.param("jobs", "four", "jobs must be an integer", id="int"),
        pytest.param("jobs", True, "jobs must be an integer", id="int-rejects-bool"),
    ],
)
def test_validate_option_types_rejects_wrong_type(
    option: str,
    value: Any,
    message: str,
) -> None:
    """Raise ValueError naming the option when its type is wrong.

    Args:
        option: The option name being validated.
        value: The invalid value supplied for the option.
        message: The exact error message expected.
    """
    with pytest.raises(ValueError, match=message):
        validate_option_types({option: value}, _SCHEMA)


def test_validate_option_types_reports_first_mismatch() -> None:
    """Raise for the offending option even when others are valid."""
    with pytest.raises(ValueError, match="python_version must be a string"):
        validate_option_types(
            {"strict": True, "python_version": 3.11},
            _SCHEMA,
        )
