"""Tests for the unified configuration manager."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.utils.unified_config import (
    DEFAULT_TOOL_PRIORITIES,
    GLOBAL_SETTINGS,
    ToolConfigInfo,
    ToolOrderStrategy,
    is_tool_injectable,
)


@pytest.mark.parametrize(
    ("strategy", "expected_value"),
    [
        (ToolOrderStrategy.PRIORITY, "priority"),
        (ToolOrderStrategy.CUSTOM, "custom"),
        (ToolOrderStrategy.ALPHABETICAL, "alphabetical"),
    ],
    ids=["priority", "custom", "alphabetical"],
)
def test_tool_order_strategy_values(
    strategy: ToolOrderStrategy,
    expected_value: str,
) -> None:
    """Verify ToolOrderStrategy enum members have expected string values.

    Args:
        strategy: The ToolOrderStrategy enum member to test.
        expected_value: The expected string value.
    """
    assert_that(strategy.value).is_equal_to(expected_value)


def test_default_values() -> None:
    """Verify default values are set correctly."""
    info = ToolConfigInfo(tool_name="ruff")

    assert_that(info.tool_name).is_equal_to("ruff")
    assert_that(info.native_config).is_equal_to({})
    assert_that(info.lintro_tool_config).is_equal_to({})
    assert_that(info.effective_config).is_equal_to({})
    assert_that(info.warnings).is_equal_to([])
    assert_that(info.is_injectable).is_true()


def test_line_length_setting_exists() -> None:
    """Verify line_length setting is defined."""
    assert_that(GLOBAL_SETTINGS).contains("line_length")


def test_line_length_has_tools() -> None:
    """Verify line_length has tool mappings."""
    assert_that(GLOBAL_SETTINGS["line_length"]).contains("tools")
    tools = GLOBAL_SETTINGS["line_length"]["tools"]

    assert_that(
        tools,
    ).contains("ruff")
    assert_that(
        tools,
    ).contains("black")
    assert_that(tools).contains("markdownlint")
    assert_that(tools).contains("yamllint")


def test_line_length_has_injectable_tools() -> None:
    """Verify injectable tools are defined."""
    assert_that(GLOBAL_SETTINGS["line_length"]).contains("injectable")
    injectable = GLOBAL_SETTINGS["line_length"]["injectable"]

    assert_that(injectable).contains("ruff")
    assert_that(injectable).contains("black")
    assert_that(injectable).contains("markdownlint")
    # yamllint is injectable via Lintro config generation
    assert_that(injectable).contains("yamllint")


def test_formatters_have_lower_priority_than_linters() -> None:
    """Formatters should run before linters (lower priority value)."""
    assert_that(DEFAULT_TOOL_PRIORITIES["black"]).is_less_than(
        DEFAULT_TOOL_PRIORITIES["ruff"],
    )
    assert_that(DEFAULT_TOOL_PRIORITIES["black"]).is_less_than(
        DEFAULT_TOOL_PRIORITIES["markdownlint"],
    )


@pytest.mark.parametrize(
    "tool_name",
    ["ruff", "markdownlint", "yamllint", "black"],
    ids=["ruff", "markdownlint", "yamllint", "black"],
)
def test_tool_is_injectable(tool_name: str) -> None:
    """Verify tools that support config injection.

    Args:
        tool_name: Name of the tool to check for injectability.
    """
    assert_that(is_tool_injectable(tool_name)).is_true()
