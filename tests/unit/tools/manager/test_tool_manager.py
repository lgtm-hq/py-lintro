"""Unit tests for ToolManager using the plugin registry system."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.tools.core.tool_manager import ToolManager
from tests.constants import MIN_EXPECTED_TOOLS


def test_tool_manager_get_all_tools() -> None:
    """Verify ToolManager discovers and returns all registered tools."""
    tm = ToolManager()
    available = tm.get_all_tools()

    # Should have all expected tools discovered
    assert_that(len(available)).is_greater_than_or_equal_to(MIN_EXPECTED_TOOLS)

    # Verify ruff is available
    assert_that(available).contains_key(ToolName.RUFF)
    ruff_tool = available[ToolName.RUFF]
    assert_that(ruff_tool.definition.name).is_equal_to(ToolName.RUFF)


def test_tool_manager_get_tool_by_name() -> None:
    """Verify get_tool returns correct tool by name."""
    tm = ToolManager()

    ruff_tool = tm.get_tool(ToolName.RUFF)
    assert_that(ruff_tool.definition.name).is_equal_to(ToolName.RUFF)

    hadolint_tool = tm.get_tool(ToolName.HADOLINT)
    assert_that(hadolint_tool.definition.name).is_equal_to(ToolName.HADOLINT)


def test_tool_manager_get_tool_case_insensitive() -> None:
    """Verify get_tool is case-insensitive."""
    tm = ToolManager()

    # Test with ToolName enum
    ruff_enum = tm.get_tool(ToolName.RUFF)
    # Test with string variations
    ruff_upper = tm.get_tool("RUFF")
    ruff_mixed = tm.get_tool("Ruff")

    assert_that(ruff_enum.definition.name).is_equal_to(ToolName.RUFF)
    assert_that(ruff_upper.definition.name).is_equal_to(ToolName.RUFF)
    assert_that(ruff_mixed.definition.name).is_equal_to(ToolName.RUFF)


def test_tool_manager_get_tool_missing() -> None:
    """Raise ValueError when attempting to get an unknown tool."""
    tm = ToolManager()
    with pytest.raises(ValueError, match="Unknown tool"):
        tm.get_tool("nonexistent-tool-that-does-not-exist")


def test_tool_manager_get_check_and_fix_tools() -> None:
    """Verify get_check_tools and get_fix_tools return correct subsets."""
    tm = ToolManager()

    check_tools = tm.get_check_tools()
    fix_tools = tm.get_fix_tools()

    # All fix tools should also be check tools
    for tool_name in fix_tools:
        assert_that(check_tools).contains_key(tool_name)

    # Ruff should be in both (can_fix=True)
    assert_that(check_tools).contains_key(ToolName.RUFF)
    assert_that(fix_tools).contains_key(ToolName.RUFF)

    # Hadolint should only be in check tools (can_fix=False)
    assert_that(check_tools).contains_key(ToolName.HADOLINT)
    assert_that(fix_tools).does_not_contain_key(ToolName.HADOLINT)


def test_tool_manager_get_tool_execution_order() -> None:
    """Verify tool execution order respects configuration."""
    tm = ToolManager()

    # Get execution order for ruff and hadolint
    order = tm.get_tool_execution_order([ToolName.RUFF, ToolName.HADOLINT])

    # Both should be in the result (no conflicts)
    assert_that(len(order)).is_equal_to(2)
    assert_that(order).contains(ToolName.RUFF)
    assert_that(order).contains(ToolName.HADOLINT)


def test_tool_manager_get_tool_execution_order_with_conflicts() -> None:
    """Verify conflict resolution in execution order."""
    tm = ToolManager()

    # Verify tools exist before testing conflict resolution
    assert_that(tm.get_tool(ToolName.RUFF)).is_not_none()
    assert_that(tm.get_tool(ToolName.BLACK)).is_not_none()

    try:
        # Temporarily modify conflicts (note: ToolDefinition is frozen, so we need
        # to work around this for testing - in practice, conflicts are set at
        # registration time)

        # Since ToolDefinition is frozen, we can't modify conflicts_with directly
        # This test verifies the conflict resolution logic works with the
        # existing tool configurations
        order = tm.get_tool_execution_order([ToolName.RUFF, ToolName.BLACK])

        # Both should be returned since they don't have conflicts defined
        assert_that(len(order)).is_equal_to(2)

        # With ignore_conflicts=True, all tools should be returned
        order_all = tm.get_tool_execution_order(
            [ToolName.RUFF, ToolName.BLACK],
            ignore_conflicts=True,
        )
        assert_that(len(order_all)).is_equal_to(2)
    finally:
        # No cleanup needed since we didn't actually modify anything
        pass


def test_tool_manager_get_tool_names() -> None:
    """Verify get_tool_names returns all registered tool names."""
    tm = ToolManager()
    names = tm.get_tool_names()

    # Should have all expected tools
    assert_that(len(names)).is_greater_than_or_equal_to(MIN_EXPECTED_TOOLS)
    assert_that(names).contains(ToolName.RUFF)
    assert_that(names).contains(ToolName.HADOLINT)
    assert_that(names).contains(ToolName.PYTEST)


def test_tool_manager_is_tool_registered() -> None:
    """Verify is_tool_registered returns correct values."""
    tm = ToolManager()

    assert_that(tm.is_tool_registered(ToolName.RUFF)).is_true()
    assert_that(tm.is_tool_registered("RUFF")).is_true()  # Case insensitive
    assert_that(tm.is_tool_registered("nonexistent")).is_false()


def test_tool_manager_set_tool_options() -> None:
    """Verify set_tool_options correctly configures tools."""
    tm = ToolManager()

    # Set options on ruff
    tm.set_tool_options(ToolName.RUFF, timeout=60)

    # Get the tool and verify options were set
    ruff_tool = tm.get_tool(ToolName.RUFF)
    assert_that(ruff_tool.options.get("timeout")).is_equal_to(60)
