"""Tests for safe_parse_items function."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.base_issue import BaseIssue
from lintro.parsers.base_parser import safe_parse_items


@dataclass
class SampleIssue(BaseIssue):
    """Simple issue for safe_parse_items tests."""

    pass


def test_safe_parse_items_success() -> None:
    """Parse all valid items successfully."""
    items: list[object] = [{"file": "a.py", "line": 1}, {"file": "b.py", "line": 2}]

    def parse_func(item: dict[str, object]) -> SampleIssue:
        # Values are validated at runtime; use explicit type checks for mypy
        line_val = item["line"]
        line = line_val if isinstance(line_val, int) else 0
        return SampleIssue(file=str(item["file"]), line=line)

    results: list[SampleIssue] = safe_parse_items(items, parse_func, "test_tool")
    assert_that(results).is_length(2)
    assert_that(results[0].file).is_equal_to("a.py")


def test_safe_parse_items_skips_non_dict() -> None:
    """Skip non-dictionary items."""
    items: list[object] = [{"file": "a.py"}, "not_a_dict", 123]

    def parse_func(item: dict[str, object]) -> SampleIssue:
        return SampleIssue(file=str(item.get("file", "")))

    with patch("lintro.parsers.base_parser.logger"):
        results: list[SampleIssue] = safe_parse_items(items, parse_func, "test_tool")
    assert_that(results).is_length(1)


def test_safe_parse_items_handles_parse_errors() -> None:
    """Continue parsing after encountering errors."""
    items: list[object] = [{"file": "a.py"}, {"bad": "data"}, {"file": "c.py"}]

    def parse_func(item: dict[str, object]) -> SampleIssue | None:
        if "file" not in item:
            raise KeyError("file")
        return SampleIssue(file=str(item["file"]))

    with patch("lintro.parsers.base_parser.logger"):
        results: list[SampleIssue] = safe_parse_items(items, parse_func, "test_tool")
    assert_that(results).is_length(2)


def test_safe_parse_items_skips_none_results() -> None:
    """Skip items where parse function returns None."""
    items: list[object] = [{"file": "a.py"}, {"skip": True}, {"file": "c.py"}]

    def parse_func(item: dict[str, object]) -> SampleIssue | None:
        if item.get("skip"):
            return None
        return SampleIssue(file=str(item.get("file", "")))

    results: list[SampleIssue] = safe_parse_items(items, parse_func, "test_tool")
    assert_that(results).is_length(2)


def test_safe_parse_items_empty_list() -> None:
    """Handle empty items list."""

    def parse_func(item: dict[str, object]) -> SampleIssue:
        return SampleIssue(file=str(item.get("file", "")))

    results: list[SampleIssue] = safe_parse_items([], parse_func, "test_tool")
    assert_that(results).is_empty()
