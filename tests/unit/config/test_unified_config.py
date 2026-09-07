"""Tests for the unified configuration manager."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.utils.unified_config import (
    GLOBAL_SETTINGS,
    ToolConfigInfo,
    is_tool_injectable,
)


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
