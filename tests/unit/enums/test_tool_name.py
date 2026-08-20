"""Tests for lintro.enums.tool_name module."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName, normalize_tool_name


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (ToolName.RUFF, "ruff"),
        (ToolName.BLACK, "black"),
        (ToolName.MYPY, "mypy"),
        (ToolName.PYTEST, "pytest"),
        (ToolName.BANDIT, "bandit"),
        (ToolName.ACTIONLINT, "actionlint"),
        (ToolName.CLIPPY, "clippy"),
        (ToolName.HADOLINT, "hadolint"),
        (ToolName.MARKDOWNLINT, "markdownlint"),
        (ToolName.YAMLLINT, "yamllint"),
    ],
)
def test_tool_name_values(member: ToolName, expected: str) -> None:
    """ToolName members have correct lowercase string values.

    Args:
        member: The ToolName enum member to test.
        expected: The expected string value.
    """
    assert_that(member.value).is_equal_to(expected)


def test_tool_name_is_str_enum() -> None:
    """ToolName members are string instances."""
    assert_that(ToolName.RUFF).is_instance_of(str)


def test_tool_name_string_comparison() -> None:
    """ToolName members compare equal to their string values."""
    # StrEnum members compare equal to their string values
    assert_that(str(ToolName.RUFF)).is_equal_to("ruff")


def test_normalize_tool_name_from_string() -> None:
    """normalize_tool_name converts string to ToolName."""
    assert_that(normalize_tool_name("ruff")).is_equal_to(ToolName.RUFF)


def test_normalize_tool_name_case_insensitive() -> None:
    """normalize_tool_name is case-insensitive."""
    assert_that(normalize_tool_name("RUFF")).is_equal_to(ToolName.RUFF)
    assert_that(normalize_tool_name("Ruff")).is_equal_to(ToolName.RUFF)


def test_normalize_tool_name_hyphenated() -> None:
    """normalize_tool_name converts hyphenated names to underscored ToolName."""
    assert_that(normalize_tool_name("astro-check")).is_equal_to(ToolName.ASTRO_CHECK)
    assert_that(normalize_tool_name("cargo-audit")).is_equal_to(ToolName.CARGO_AUDIT)


def test_normalize_tool_name_passthrough() -> None:
    """normalize_tool_name returns ToolName unchanged."""
    assert_that(normalize_tool_name(ToolName.RUFF)).is_equal_to(ToolName.RUFF)


def test_normalize_tool_name_invalid_raises() -> None:
    """normalize_tool_name raises ValueError for unknown tool."""
    with pytest.raises(ValueError, match="Unknown tool name"):
        normalize_tool_name("nonexistent")


def test_tool_name_aliases_include_hyphen_and_underscore() -> None:
    """tool_name_aliases returns both spellings without duplicates."""
    from lintro.enums.tool_name import tool_name_aliases

    assert_that(tool_name_aliases("astro_check")).is_equal_to(
        ("astro_check", "astro-check"),
    )
    assert_that(tool_name_aliases("astro-check")).is_equal_to(
        ("astro-check", "astro_check"),
    )
    assert_that(tool_name_aliases("ruff")).is_equal_to(("ruff",))
