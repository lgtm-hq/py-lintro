"""Tests for lintro.parsers.base_parser module."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from assertpy import assert_that

from lintro.parsers.base_issue import BaseIssue
from lintro.parsers.base_parser import (
    collect_continuation_lines,
    extract_dict_field,
    extract_int_field,
    extract_str_field,
    safe_parse_items,
    strip_ansi_codes,
    validate_int_field,
    validate_str_field,
)


def test_extract_int_field_first_candidate() -> None:
    """extract_int_field returns value from first matching candidate."""
    data: dict[str, object] = {"line": 10, "row": 20}
    result = extract_int_field(data, ["line", "row"])
    assert_that(result).is_equal_to(10)


def test_extract_int_field_second_candidate() -> None:
    """extract_int_field falls back to second candidate."""
    data: dict[str, object] = {"row": 20}
    result = extract_int_field(data, ["line", "row"])
    assert_that(result).is_equal_to(20)


def test_extract_int_field_default() -> None:
    """extract_int_field returns default when no match."""
    data: dict[str, object] = {"other": 5}
    result = extract_int_field(data, ["line", "row"], default=0)
    assert_that(result).is_equal_to(0)


def test_extract_int_field_none_default() -> None:
    """extract_int_field returns None default."""
    data: dict[str, object] = {}
    result = extract_int_field(data, ["line"])
    assert_that(result).is_none()


def test_extract_int_field_excludes_bool() -> None:
    """extract_int_field excludes boolean values."""
    data: dict[str, object] = {"line": True}
    result = extract_int_field(data, ["line"], default=0)
    assert_that(result).is_equal_to(0)


def test_extract_str_field_default() -> None:
    """extract_str_field returns default when no match."""
    data: dict[str, object] = {"other": "value"}
    result = extract_str_field(data, ["filename", "file"], default="unknown")
    assert_that(result).is_equal_to("unknown")


def test_extract_str_field_empty_default() -> None:
    """extract_str_field returns empty string default."""
    data: dict[str, object] = {}
    result = extract_str_field(data, ["filename"])
    assert_that(result).is_equal_to("")


def test_extract_dict_field_default() -> None:
    """extract_dict_field returns default when no match."""
    data: dict[str, object] = {"other": "value"}
    result = extract_dict_field(data, ["location"], default={"line": 0})
    assert_that(result).is_equal_to({"line": 0})


def test_extract_dict_field_empty_default() -> None:
    """extract_dict_field returns empty dict default."""
    data: dict[str, object] = {}
    result = extract_dict_field(data, ["location"])
    assert_that(result).is_equal_to({})


def test_strip_ansi_codes_plain_text() -> None:
    """strip_ansi_codes returns plain text unchanged."""
    text = "plain text without codes"
    result = strip_ansi_codes(text)
    assert_that(result).is_equal_to("plain text without codes")


def test_strip_ansi_codes_multiple_codes() -> None:
    """strip_ansi_codes handles multiple ANSI codes."""
    text = "\x1b[1m\x1b[32mSuccess\x1b[0m: \x1b[34minfo\x1b[0m"
    result = strip_ansi_codes(text)
    assert_that(result).is_equal_to("Success: info")


def test_validate_str_field_valid_string() -> None:
    """validate_str_field returns string value."""
    result = validate_str_field("test", "field")
    assert_that(result).is_equal_to("test")


def test_validate_str_field_non_string_returns_default() -> None:
    """validate_str_field returns default for non-string."""
    result = validate_str_field(123, "field", default="default")
    assert_that(result).is_equal_to("default")


def test_validate_str_field_none_returns_default() -> None:
    """validate_str_field returns default for None."""
    result = validate_str_field(None, "field", default="default")
    assert_that(result).is_equal_to("default")


def test_validate_int_field_valid_int() -> None:
    """validate_int_field returns integer value."""
    result = validate_int_field(42, "field")
    assert_that(result).is_equal_to(42)


def test_validate_int_field_non_int_returns_default() -> None:
    """validate_int_field returns default for non-integer."""
    result = validate_int_field("not_int", "field", default=0)
    assert_that(result).is_equal_to(0)


def test_validate_int_field_bool_returns_default() -> None:
    """validate_int_field returns default for boolean."""
    result = validate_int_field(True, "field", default=0)
    assert_that(result).is_equal_to(0)


def test_validate_int_field_none_returns_default() -> None:
    """validate_int_field returns default for None."""
    result = validate_int_field(None, "field", default=0)
    assert_that(result).is_equal_to(0)


def test_collect_continuation_lines_basic() -> None:
    """collect_continuation_lines collects indented lines."""
    lines = ["main message", "    continued", "    more", "next item"]
    result, next_idx = collect_continuation_lines(
        lines,
        1,
        lambda line: line.startswith("    "),
    )
    assert_that(result).is_equal_to("continued more")
    assert_that(next_idx).is_equal_to(3)


def test_collect_continuation_lines_no_continuation() -> None:
    """collect_continuation_lines handles no continuation."""
    lines = ["main message", "next item"]
    result, next_idx = collect_continuation_lines(
        lines,
        1,
        lambda line: line.startswith("    "),
    )
    assert_that(result).is_equal_to("")
    assert_that(next_idx).is_equal_to(1)


def test_collect_continuation_lines_end_of_list() -> None:
    """collect_continuation_lines handles end of list."""
    lines = ["main message", "    continued"]
    result, next_idx = collect_continuation_lines(
        lines,
        1,
        lambda line: line.startswith("    "),
    )
    assert_that(result).is_equal_to("continued")
    assert_that(next_idx).is_equal_to(2)


def test_collect_continuation_lines_strips_prefix() -> None:
    """collect_continuation_lines strips colon prefix."""
    lines = ["message", ": continued part"]
    result, next_idx = collect_continuation_lines(
        lines,
        1,
        lambda _: True,
    )
    assert_that(result).is_equal_to("continued part")


def test_safe_parse_items_valid_items() -> None:
    """safe_parse_items parses valid dictionaries."""

    @dataclass
    class TestIssue(BaseIssue):
        pass

    def parse_func(item: dict[str, object]) -> TestIssue | None:
        file = item.get("file", "")
        return TestIssue(file=str(file) if file else "")

    items: list[object] = [{"file": "a.py"}, {"file": "b.py"}]
    result = safe_parse_items(items, parse_func, "test")
    assert_that(len(result)).is_equal_to(2)
    assert_that(result[0].file).is_equal_to("a.py")
    assert_that(result[1].file).is_equal_to("b.py")


def test_safe_parse_items_skips_non_dict() -> None:
    """safe_parse_items skips non-dictionary items."""

    @dataclass
    class TestIssue(BaseIssue):
        pass

    def parse_func(item: dict[str, object]) -> TestIssue | None:
        file = item.get("file", "")
        return TestIssue(file=str(file) if file else "")

    items: list[object] = [{"file": "a.py"}, "invalid", 123]
    result = safe_parse_items(items, parse_func, "test")
    assert_that(len(result)).is_equal_to(1)


def test_safe_parse_items_handles_parse_failure() -> None:
    """safe_parse_items handles parse function exceptions."""

    @dataclass
    class TestIssue(BaseIssue):
        pass

    def parse_func(item: dict[str, object]) -> TestIssue | None:
        if "error" in item:
            raise ValueError("Parse error")
        file = item.get("file", "")
        return TestIssue(file=str(file) if file else "")

    items: list[object] = [{"file": "a.py"}, {"error": True}, {"file": "b.py"}]
    result = safe_parse_items(items, parse_func, "test")
    assert_that(len(result)).is_equal_to(2)


def test_safe_parse_items_handles_none_return() -> None:
    """safe_parse_items filters out None returns."""

    @dataclass
    class TestIssue(BaseIssue):
        pass

    def parse_func(item: dict[str, object]) -> TestIssue | None:
        if item.get("skip"):
            return None
        file = item.get("file", "")
        return TestIssue(file=str(file) if file else "")

    items: list[object] = [{"file": "a.py"}, {"skip": True}, {"file": "b.py"}]
    result = safe_parse_items(items, parse_func, "test")
    assert_that(len(result)).is_equal_to(2)


def test_safe_parse_items_empty_list() -> None:
    """safe_parse_items handles empty list."""

    @dataclass
    class TestIssue(BaseIssue):
        pass

    def parse_func(item: dict[str, object]) -> TestIssue | None:
        return TestIssue()

    result = safe_parse_items([], parse_func, "test")
    assert_that(result).is_empty()


@pytest.mark.parametrize(
    ("data", "candidates", "expected"),
    [
        ({"a": 1, "b": 2}, ["a"], 1),
        ({"a": 1, "b": 2}, ["c", "b"], 2),
        ({"a": 1, "b": 2}, ["c", "d"], None),
    ],
)
def test_extract_int_field_parametrized(
    data: dict[str, object],
    candidates: list[str],
    expected: int | None,
) -> None:
    """extract_int_field handles various scenarios.

    Args:
        data: Dictionary to extract from.
        candidates: List of candidate keys.
        expected: Expected result value.
    """
    result = extract_int_field(data, candidates)
    assert_that(result).is_equal_to(expected)


@pytest.mark.parametrize(
    ("data", "candidates", "expected"),
    [
        ({"a": "x", "b": "y"}, ["a"], "x"),
        ({"a": "x", "b": "y"}, ["c", "b"], "y"),
        ({"a": "x", "b": "y"}, ["c", "d"], ""),
    ],
)
def test_extract_str_field_parametrized(
    data: dict[str, object],
    candidates: list[str],
    expected: str,
) -> None:
    """extract_str_field handles various scenarios.

    Args:
        data: Dictionary to extract from.
        candidates: List of candidate keys.
        expected: Expected result value.
    """
    result = extract_str_field(data, candidates)
    assert_that(result).is_equal_to(expected)
