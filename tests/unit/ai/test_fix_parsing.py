"""Tests for parse_fix_response, parse_batch_response, and generate_diff."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.ai.fix_parsing import (
    generate_diff,
    parse_batch_response,
    parse_fix_response,
)

# ---------------------------------------------------------------------------
# generate_diff
# ---------------------------------------------------------------------------


def test_generate_diff_generates_unified_diff():
    """Verify unified diff output contains expected file headers and change markers."""
    diff = generate_diff("test.py", "old code\n", "new code\n")
    assert_that(diff).contains("a/test.py")
    assert_that(diff).contains("b/test.py")
    assert_that(diff).contains("-old code")
    assert_that(diff).contains("+new code")


def test_generate_diff_no_diff_for_identical():
    """Verify that identical content produces an empty diff string."""
    diff = generate_diff("test.py", "same\n", "same\n")
    assert_that(diff).is_equal_to("")


# ---------------------------------------------------------------------------
# parse_fix_response
# ---------------------------------------------------------------------------


def test_parse_fix_response_valid_response():
    """Valid JSON is parsed into a fix suggestion with correct fields."""
    content = json.dumps(
        {
            "original_code": "assert x > 0",
            "suggested_code": "if not x > 0:\n    raise ValueError",
            "explanation": "Replace assert",
            "confidence": "high",
        },
    )
    result = parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert_that(result.file).is_equal_to("main.py")  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    assert_that(result.confidence).is_equal_to("high")  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    assert_that(result.diff).is_not_empty()  # type: ignore[union-attr]  # assertpy is_not_none narrows this


def test_parse_fix_response_non_object_json():
    """Non-object JSON (array, string, number) returns None."""
    for payload in ["[1, 2]", '"just a string"', "42"]:
        result = parse_fix_response(payload, "main.py", 10, "B101")
        assert_that(result).is_none()


def test_parse_fix_response_non_string_code_fields():
    """Non-string original_code or suggested_code returns None."""
    content = json.dumps(
        {
            "original_code": 123,
            "suggested_code": ["not", "a", "string"],
            "explanation": "Fix",
            "confidence": "medium",
        },
    )
    result = parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_none()


def test_parse_fix_response_invalid_json():
    """Verify that invalid JSON content returns None."""
    result = parse_fix_response("not json", "main.py", 10, "B101")
    assert_that(result).is_none()


def test_parse_fix_response_identical_code():
    """Verify that identical original and suggested code returns None."""
    content = json.dumps(
        {
            "original_code": "x = 1",
            "suggested_code": "x = 1",
            "explanation": "No change",
            "confidence": "high",
        },
    )
    result = parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_none()


def test_parse_fix_response_empty_original():
    """Verify that an empty original_code field returns None."""
    content = json.dumps(
        {
            "original_code": "",
            "suggested_code": "new code",
            "explanation": "Fix",
            "confidence": "medium",
        },
    )
    result = parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_none()


def test_parse_fix_response_empty_suggested():
    """Verify that an empty suggested_code field returns None."""
    content = json.dumps(
        {
            "original_code": "old code",
            "suggested_code": "",
            "explanation": "Fix",
            "confidence": "medium",
        },
    )
    result = parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_none()


def test_parse_fix_response_extracts_risk_level():
    """parse_fix_response should populate risk_level from the JSON payload."""
    content = json.dumps(
        {
            "original_code": "assert x > 0",
            "suggested_code": "if not x > 0:\n    raise ValueError",
            "explanation": "Replace assert",
            "confidence": "high",
            "risk_level": "low",
        },
    )
    result = parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert_that(result.risk_level).is_equal_to("low")  # type: ignore[union-attr]  # assertpy is_not_none narrows this


def test_parse_fix_response_risk_level_defaults_to_empty():
    """When risk_level is absent from the JSON, the field should default to ''."""
    content = json.dumps(
        {
            "original_code": "assert x > 0",
            "suggested_code": "if not x > 0:\n    raise ValueError",
            "explanation": "Replace assert",
            "confidence": "high",
        },
    )
    result = parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert_that(result.risk_level).is_equal_to("")  # type: ignore[union-attr]  # assertpy is_not_none narrows this


def test_parse_fix_response_confidence_defaults_to_medium():
    """When confidence is absent from the JSON, the field should default to 'medium'."""
    content = json.dumps(
        {
            "original_code": "assert x > 0",
            "suggested_code": "if not x > 0:\n    raise ValueError",
            "explanation": "Replace assert",
        },
    )
    result = parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert_that(result.confidence).is_equal_to("medium")  # type: ignore[union-attr]  # assertpy is_not_none narrows this


# ---------------------------------------------------------------------------
# parse_batch_response
# ---------------------------------------------------------------------------


def test_parse_batch_response_valid():
    """Valid batch JSON array is parsed into suggestions."""
    content = json.dumps(
        [
            {
                "line": 5,
                "code": "E501",
                "original_code": "old",
                "suggested_code": "new",
                "explanation": "Fix",
                "confidence": "high",
                "risk_level": "safe-style",
            },
        ],
    )
    result = parse_batch_response(content, "test.py")
    assert_that(result).is_length(1)
    assert_that(result[0].line).is_equal_to(5)
    assert_that(result[0].code).is_equal_to("E501")
    assert_that(result[0].risk_level).is_equal_to("safe-style")


def test_parse_batch_response_invalid_json():
    """Invalid JSON returns empty list."""
    result = parse_batch_response("not json", "test.py")
    assert_that(result).is_empty()


def test_parse_batch_response_not_array():
    """Non-array JSON returns empty list."""
    result = parse_batch_response('{"key": "value"}', "test.py")
    assert_that(result).is_empty()


def test_parse_batch_response_mixed_valid_and_invalid():
    """Only valid items are returned; invalid items are skipped."""
    content = json.dumps(
        [
            # Valid item
            {
                "line": 10,
                "code": "E501",
                "original_code": "old line",
                "suggested_code": "new line",
                "explanation": "Fix",
                "confidence": "high",
                "risk_level": "safe-style",
            },
            # Non-dict item (string)
            "not a dict",
            # Null item
            None,
            # Missing suggested_code
            {
                "line": 20,
                "code": "E502",
                "original_code": "code",
            },
            # Identical original and suggested
            {
                "line": 30,
                "code": "E503",
                "original_code": "same",
                "suggested_code": "same",
            },
            # Non-string code fields
            {
                "line": 40,
                "code": "E504",
                "original_code": 123,
                "suggested_code": ["list"],
            },
        ],
    )
    result = parse_batch_response(content, "test.py")
    assert_that(result).is_length(1)
    assert_that(result[0].line).is_equal_to(10)
    assert_that(result[0].code).is_equal_to("E501")
    assert_that(result[0].risk_level).is_equal_to("safe-style")


def test_parse_batch_response_skips_identical_code():
    """Items with identical original and suggested code are skipped."""
    content = json.dumps(
        [
            {
                "line": 1,
                "code": "E501",
                "original_code": "same",
                "suggested_code": "same",
                "explanation": "No change",
                "confidence": "high",
            },
        ],
    )
    result = parse_batch_response(content, "test.py")
    assert_that(result).is_empty()


def test_parse_batch_response_coerces_line_and_code():
    """Verify line is coerced to int and code to str from non-standard types."""
    content = json.dumps(
        [
            {
                "line": "7",
                "code": 123,
                "original_code": "old",
                "suggested_code": "new",
            },
            {
                "line": "notanint",
                "code": None,
                "original_code": "old2",
                "suggested_code": "new2",
            },
        ],
    )
    result = parse_batch_response(content, "test.py")
    assert_that(result).is_length(2)
    # Numeric string coerced to int
    assert_that(result[0].line).is_equal_to(7)
    assert_that(result[0].code).is_equal_to("123")
    # Non-numeric string falls back to 0
    assert_that(result[1].line).is_equal_to(0)
    assert_that(result[1].code).is_equal_to("None")
