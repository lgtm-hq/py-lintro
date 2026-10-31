"""Tests for parse_fix_response, parse_batch_response, and generate_diff."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.ai.cli_schemas import FIX_BATCH_KEY
from lintro.ai.enums import ConfidenceLevel, RiskLevel
from lintro.ai.fix_parsing import (
    AIFixParseError,
    generate_diff,
    parse_batch_response,
    parse_fix_response,
)
from lintro.ai.metadata import suggestion_to_payload
from lintro.ai.models import AIFixSuggestion

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


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        '"just a string"',
        "42",
        "null",
        "{}",
        '{"original_code": "old"}',
        '{"original_code": null, "suggested_code": "new"}',
        '{"original_code": "old", "suggested_code": []}',
    ],
)
def test_parse_fix_response_rejects_malformed_responses(content):
    """Broken JSON, wrong shapes, and missing or non-string code raise."""
    assert_that(parse_fix_response).raises(AIFixParseError).when_called_with(
        content,
        "main.py",
        10,
        "B101",
    )


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
            "risk_level": "safe-style",
        },
    )
    result = parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert_that(result.risk_level).is_same_as(RiskLevel.SAFE_STYLE)  # type: ignore[union-attr]  # assertpy is_not_none narrows this


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


@pytest.mark.parametrize("shape", ["single", "batch", "wrapped"])
@pytest.mark.parametrize("metadata", [None, 42, False, [], {}])
def test_fix_metadata_defaults_survive_serialization(shape, metadata):
    """Null and non-string metadata use the same defaults as omitted fields."""
    entry = {
        "original_code": "x = 1",
        "suggested_code": "x = 2",
        "confidence": metadata,
        "explanation": metadata,
        "risk_level": metadata,
    }
    suggestions: list[AIFixSuggestion | None] = []
    if shape == "single":
        suggestions.append(parse_fix_response(json.dumps(entry), "main.py", 1, "B101"))
    else:
        payload = [entry] if shape == "batch" else {FIX_BATCH_KEY: [entry]}
        suggestions.extend(parse_batch_response(json.dumps(payload), "main.py"))

    assert_that(suggestions).is_length(1)
    suggestion = suggestions[0]
    if suggestion is None:
        pytest.fail("Expected a suggestion for a non-empty patch")
    assert_that(suggestion.confidence).is_same_as(ConfidenceLevel.MEDIUM)
    assert_that(suggestion.explanation).is_equal_to("")
    assert_that(suggestion.risk_level).is_equal_to("")
    payload = suggestion_to_payload(suggestion).to_dict()
    assert_that(list(payload.values())).does_not_contain(None)
    assert_that(json.loads(json.dumps(payload))).contains_entry(
        {"confidence": "medium"},
        {"explanation": ""},
        {"risk_level": ""},
    )


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("confidence", list(ConfidenceLevel))
@pytest.mark.parametrize("risk", list(RiskLevel))
def test_fix_metadata_uses_existing_enums(batch, confidence, risk):
    """Recognized labels become enums without losing explanation text."""
    entry = {
        "original_code": "x = 1",
        "suggested_code": "x = 2",
        "confidence": confidence.value,
        "risk_level": risk.value,
        "explanation": "Change the value",
    }
    result: AIFixSuggestion | None
    if batch:
        result = parse_batch_response(json.dumps([entry]), "main.py")[0]
    else:
        result = parse_fix_response(json.dumps(entry), "main.py", 1, "B101")
    if result is None:
        pytest.fail("Expected a suggestion for a non-empty patch")
    assert_that(result.confidence).is_same_as(confidence)
    assert_that(result.risk_level).is_same_as(risk)
    assert_that(result.explanation).is_equal_to("Change the value")


@pytest.mark.parametrize("label", ["", "unknown", "HIGH"])
def test_unknown_metadata_labels_keep_defaults(label):
    """Unrecognized strings cannot become confidence or safe-risk claims."""
    content = json.dumps(
        [
            {
                "original_code": "old",
                "suggested_code": "new",
                "confidence": label,
                "risk_level": label,
            },
        ],
    )
    suggestion = parse_batch_response(content, "main.py")[0]
    assert_that(suggestion.confidence).is_same_as(ConfidenceLevel.MEDIUM)
    assert_that(suggestion.risk_level).is_equal_to("")


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


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"key": "value"}',
        "null",
        "42",
        '{"fixes": null}',
        '{"fixes": {"line": 5}}',
    ],
)
def test_parse_batch_response_rejects_malformed_containers(content):
    """Malformed containers raise rather than reporting measured absence."""
    assert_that(parse_batch_response).raises(AIFixParseError).when_called_with(
        content,
        "test.py",
    )


@pytest.mark.parametrize(
    "item",
    [
        "not a dict",
        None,
        {},
        {"original_code": "code"},
        {"original_code": 123, "suggested_code": ["list"]},
    ],
)
def test_parse_batch_response_rejects_malformed_entries(item):
    """A malformed entry fails even a batch containing another valid fix."""
    content = json.dumps([_BATCH_ENTRY, item])
    assert_that(parse_batch_response).raises(AIFixParseError).when_called_with(
        content,
        "test.py",
    )


@pytest.mark.parametrize(
    "original,suggested",
    [
        ("same", "same"),
        ("", "new"),
        ("old", ""),
        ("", ""),
    ],
)
def test_parse_batch_response_skips_empty_or_identical_code(original, suggested):
    """Empty and no-op patches are valid but offer no useful suggestion."""
    content = json.dumps(
        [
            {
                "line": 1,
                "code": "E501",
                "original_code": original,
                "suggested_code": suggested,
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


#: One batch entry in the shape the CLI fix schema requires (#2573).
_BATCH_ENTRY = {
    "line": 5,
    "code": "E501",
    "original_code": "old",
    "suggested_code": "new",
    "explanation": "Fix",
    "confidence": "high",
    "risk_level": "safe-style",
}


def test_parse_batch_response_accepts_the_wrapped_cli_schema_object():
    """The object the CLI schema now requests is unwrapped (#2573).

    ``FIX_BATCH_CLI_SCHEMA`` wraps the array in an object because the CLI
    transport rejects an array at the root, so this is the shape every CLI
    ``--fix`` response now arrives in.
    """
    content = json.dumps({FIX_BATCH_KEY: [_BATCH_ENTRY]})

    result = parse_batch_response(content, "test.py")

    assert_that(result).is_length(1)
    assert_that(result[0].line).is_equal_to(5)
    assert_that(result[0].suggested_code).is_equal_to("new")


def test_parse_batch_response_still_accepts_a_bare_array():
    """Older transcripts and caches hold a bare array and must still parse."""
    content = json.dumps([_BATCH_ENTRY])

    result = parse_batch_response(content, "test.py")

    assert_that(result).is_length(1)
    assert_that(result[0].line).is_equal_to(5)


@pytest.mark.parametrize("content", ["[]", '{"fixes": []}'])
def test_parse_batch_response_accepts_empty_arrays(content):
    """Explicit empty arrays remain a valid measured absence of fixes."""
    assert_that(parse_batch_response(content, "test.py")).is_empty()
