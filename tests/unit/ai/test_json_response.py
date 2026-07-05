"""Tests for JSON response parsing helpers."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.json_response import load_json_object, strip_json_fences


def test_strip_json_fences_removes_markdown_wrapper() -> None:
    """Fence stripper extracts JSON from markdown code blocks."""
    content = '```json\n{"summary": "ok"}\n```'
    assert_that(strip_json_fences(content=content)).is_equal_to('{"summary": "ok"}')


def test_strip_json_fences_passthrough_without_fences() -> None:
    """Plain JSON passes through unchanged."""
    content = '{"summary": "ok"}'
    assert_that(strip_json_fences(content=content)).is_equal_to(content)


def test_load_json_object_parses_fenced_payload() -> None:
    """load_json_object strips fences and parses objects."""
    payload = load_json_object(content='```json\n{"a": 1}\n```')
    assert_that(payload).is_equal_to({"a": 1})


def test_load_json_object_rejects_invalid_json() -> None:
    """Invalid JSON raises ValueError with a clear message."""
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_json_object(content="not json")


def test_load_json_object_rejects_non_object() -> None:
    """Array JSON raises ValueError."""
    with pytest.raises(ValueError, match="must be an object"):
        load_json_object(content="[1, 2, 3]")


def test_strip_json_fences_prefers_last_parseable_block() -> None:
    """Prose with a decoy fenced block before real JSON extracts the payload."""
    content = (
        "Here is an example of the shape:\n"
        "```json\n"
        '{"summary": "decoy"}\n'
        "```\n"
        "And here is my actual review:\n"
        "```json\n"
        '{"summary": "real", "findings": []}\n'
        "```\n"
    )

    stripped = strip_json_fences(content=content)

    assert_that(stripped).is_equal_to('{"summary": "real", "findings": []}')


def test_strip_json_fences_skips_non_json_decoy_fence() -> None:
    """A non-JSON fenced snippet before the payload is ignored."""
    content = (
        "First run this:\n"
        "```bash\n"
        "git diff HEAD~1\n"
        "```\n"
        "```json\n"
        '{"summary": "ok"}\n'
        "```\n"
    )

    stripped = strip_json_fences(content=content)

    assert_that(stripped).is_equal_to('{"summary": "ok"}')


def test_strip_json_fences_falls_back_to_brace_matching() -> None:
    """Unfenced prose wrapping a JSON object extracts via brace matching."""
    content = 'Sure! Here is the result: {"summary": "ok", "findings": []} Done.'

    stripped = strip_json_fences(content=content)

    assert_that(stripped).is_equal_to('{"summary": "ok", "findings": []}')


def test_strip_json_fences_brace_matching_ignores_braces_in_strings() -> None:
    """Brace matching respects braces embedded in string literals."""
    content = 'prefix {"text": "a } b", "n": 1} suffix'

    stripped = strip_json_fences(content=content)

    assert_that(stripped).is_equal_to('{"text": "a } b", "n": 1}')


def test_strip_json_fences_skips_decoy_array_before_object() -> None:
    """A decoy array span before the real object is not returned as payload."""
    content = 'Checklist [1]\n{"summary": "real", "findings": []}'

    stripped = strip_json_fences(content=content, expect_object=True)

    assert_that(stripped).is_equal_to('{"summary": "real", "findings": []}')


def test_load_json_object_skips_decoy_array_before_object() -> None:
    """load_json_object recovers the real object past an earlier decoy array."""
    payload = load_json_object(
        content='Checklist [1]\n{"summary": "real", "findings": []}',
    )

    assert_that(payload).is_equal_to({"summary": "real", "findings": []})
