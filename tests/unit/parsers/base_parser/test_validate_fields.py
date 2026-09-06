"""Tests for field validation functions."""

from __future__ import annotations

from assertpy import assert_that
from loguru import logger

from lintro.parsers.base_parser import validate_int_field, validate_str_field

# === Validate Str Field Tests ===


def test_validate_str_field_valid_string() -> None:
    """Return string value unchanged."""
    result = validate_str_field("test", "field_name")
    assert_that(result).is_equal_to("test")


def test_validate_str_field_non_string_returns_default() -> None:
    """Return default for non-string values."""
    result = validate_str_field(123, "field_name", default="unknown")
    assert_that(result).is_equal_to("unknown")


def test_validate_str_field_none_returns_default() -> None:
    """Return default for None values."""
    result = validate_str_field(None, "field_name", default="default")
    assert_that(result).is_equal_to("default")


def _captured_warnings(value: object) -> list[str]:
    """Validate a value as a string field and return the warnings it emitted.

    Args:
        value: Value handed to :func:`validate_str_field`.

    Returns:
        list[str]: Warning messages emitted during validation.
    """
    warnings: list[str] = []
    handler_id = logger.add(
        lambda message: warnings.append(message.record["message"]),
        level="WARNING",
    )
    try:
        validate_str_field(value, "test_field", log_warning=True)
    finally:
        logger.remove(handler_id)
    return warnings


def test_validate_str_field_logs_warning() -> None:
    """Log warning when log_warning is True and type mismatches."""
    warnings = _captured_warnings(value=123)

    assert_that(warnings).is_length(1)
    assert_that(warnings[0]).contains("test_field")
    assert_that(warnings[0]).contains("int")


def test_validate_str_field_no_warning_for_none() -> None:
    """Do not log warning for None values."""
    assert_that(_captured_warnings(value=None)).is_empty()


# === Validate Int Field Tests ===


def test_validate_int_field_valid_int() -> None:
    """Return integer value unchanged."""
    result = validate_int_field(42, "field_name")
    assert_that(result).is_equal_to(42)


def test_validate_int_field_non_int_returns_default() -> None:
    """Return default for non-integer values."""
    result = validate_int_field("not_int", "field_name", default=0)
    assert_that(result).is_equal_to(0)


def test_validate_int_field_bool_returns_default() -> None:
    """Return default for boolean values (bools are not treated as ints)."""
    result = validate_int_field(True, "field_name", default=-1)
    assert_that(result).is_equal_to(-1)


def test_validate_int_field_none_returns_default() -> None:
    """Return default for None values."""
    result = validate_int_field(None, "field_name", default=99)
    assert_that(result).is_equal_to(99)


def test_validate_int_field_logs_warning() -> None:
    """Log warning when log_warning is True and type mismatches."""
    warnings: list[str] = []
    handler_id = logger.add(
        lambda message: warnings.append(message.record["message"]),
        level="WARNING",
    )
    try:
        validate_int_field("bad", "line_number", log_warning=True)
    finally:
        logger.remove(handler_id)

    assert_that(warnings).is_length(1)
    assert_that(warnings[0]).contains("line_number")
