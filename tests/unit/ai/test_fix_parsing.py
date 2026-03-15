"""Tests for parse_fix_response, parse_batch_response, and generate_diff."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.ai.fix_parsing import (
    generate_diff as _generate_diff,
)
from lintro.ai.fix_parsing import (
    parse_batch_response as _parse_batch_response,
)
from lintro.ai.fix_parsing import (
    parse_fix_response as _parse_fix_response,
)

# ---------------------------------------------------------------------------
# _generate_diff
# ---------------------------------------------------------------------------


def test_generate_diff_generates_unified_diff():
    """Verify unified diff output contains expected file headers and change markers."""
    diff = _generate_diff("test.py", "old code\n", "new code\n")
    assert_that(diff).contains("a/test.py")
    assert_that(diff).contains("b/test.py")
    assert_that(diff).contains("-old code")
    assert_that(diff).contains("+new code")


def test_generate_diff_no_diff_for_identical():
    """Verify that identical content produces an empty diff string."""
    diff = _generate_diff("test.py", "same\n", "same\n")
    assert_that(diff).is_equal_to("")


# ---------------------------------------------------------------------------
# _parse_fix_response
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
    result = _parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert_that(result.file).is_equal_to("main.py")  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    assert_that(result.confidence).is_equal_to("high")  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    assert_that(result.diff).is_not_empty()  # type: ignore[union-attr]  # assertpy is_not_none narrows this


def test_parse_fix_response_invalid_json():
    """Verify that invalid JSON content returns None."""
    result = _parse_fix_response("not json", "main.py", 10, "B101")
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
    result = _parse_fix_response(content, "main.py", 10, "B101")
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
    result = _parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_none()


def test_parse_fix_response_extracts_risk_level():
    """_parse_fix_response should populate risk_level from the JSON payload."""
    content = json.dumps(
        {
            "original_code": "assert x > 0",
            "suggested_code": "if not x > 0:\n    raise ValueError",
            "explanation": "Replace assert",
            "confidence": "high",
            "risk_level": "low",
        },
    )
    result = _parse_fix_response(content, "main.py", 10, "B101")
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
    result = _parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert_that(result.risk_level).is_equal_to("")  # type: ignore[union-attr]  # assertpy is_not_none narrows this


# ---------------------------------------------------------------------------
# _parse_batch_response
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
    result = _parse_batch_response(content, "test.py")
    assert_that(result).is_length(1)
    assert_that(result[0].line).is_equal_to(5)
    assert_that(result[0].code).is_equal_to("E501")
    assert_that(result[0].risk_level).is_equal_to("safe-style")


def test_parse_batch_response_invalid_json():
    """Invalid JSON returns empty list."""
    result = _parse_batch_response("not json", "test.py")
    assert_that(result).is_empty()


def test_parse_batch_response_not_array():
    """Non-array JSON returns empty list."""
    result = _parse_batch_response('{"key": "value"}', "test.py")
    assert_that(result).is_empty()


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
    result = _parse_batch_response(content, "test.py")
    assert_that(result).is_empty()
