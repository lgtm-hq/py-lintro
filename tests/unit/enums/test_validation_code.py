"""Tests for the config-validate JSON code contract."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.validation_code import ValidationCode


def test_validation_code_json_values_are_snake_case() -> None:
    """Each ValidationCode.value is the lowercase snake_case JSON string."""
    assert_that(ValidationCode.NOT_FOUND.value).is_equal_to("not_found")
    assert_that(ValidationCode.PARSE_ERROR.value).is_equal_to("parse_error")
    assert_that(ValidationCode.EMPTY_CONFIG.value).is_equal_to("empty_config")
    assert_that(ValidationCode.INVALID_TYPE.value).is_equal_to("invalid_type")
    assert_that(ValidationCode.UNKNOWN_OPTION.value).is_equal_to("unknown_option")
    assert_that(ValidationCode.DEPRECATED_OPTION.value).is_equal_to(
        "deprecated_option",
    )
    assert_that(ValidationCode.UNKNOWN_TOOL.value).is_equal_to("unknown_tool")
    assert_that(ValidationCode.MISSING_DEPENDENCY.value).is_equal_to(
        "missing_dependency",
    )


def test_validation_code_members_match_explicit_vocabulary() -> None:
    """The public JSON vocabulary must stay exactly these eight codes."""
    assert_that({member.value for member in ValidationCode}).is_equal_to(
        {
            "not_found",
            "parse_error",
            "empty_config",
            "invalid_type",
            "unknown_option",
            "deprecated_option",
            "unknown_tool",
            "missing_dependency",
        },
    )
