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
