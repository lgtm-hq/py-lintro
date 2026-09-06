"""Unit tests for plugins/registry module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import ToolRegistry, register_tool
from tests.unit.plugins.conftest import create_fake_plugin

# =============================================================================
# Tests for ToolRegistry.register
# =============================================================================


def test_register_tool_adds_to_registry() -> None:
    """Verify that registering a tool class adds it to the registry."""
    plugin_class = create_fake_plugin(name="test-register-tool")
    ToolRegistry.register(plugin_class)

    assert_that(ToolRegistry.is_registered("test-register-tool")).is_true()


def test_register_tool_stores_instance() -> None:
    """Verify that registering a tool creates and stores an instance."""
    plugin_class = create_fake_plugin(name="test-instance-tool")
    ToolRegistry.register(plugin_class)

    tool = ToolRegistry.get("test-instance-tool")
    assert_that(tool).is_instance_of(BaseToolPlugin)
    assert_that(tool.definition.name).is_equal_to("test-instance-tool")


def test_register_overwrite_replaces_tool() -> None:
    """Verify that registering a tool with the same name replaces the previous one."""
    plugin_class1 = create_fake_plugin(name="overwrite-test", description="First")
    plugin_class2 = create_fake_plugin(name="overwrite-test", description="Second")

    ToolRegistry.register(plugin_class1)
    ToolRegistry.register(plugin_class2)

    assert_that(ToolRegistry.is_registered("overwrite-test")).is_true()
    # The second registration should have replaced the first
    tool = ToolRegistry.get("overwrite-test")
    assert_that(tool.definition.description).is_equal_to("Second")


def test_register_returns_class_unchanged() -> None:
    """Verify that register returns the class unchanged for decorator use."""
    plugin_class = create_fake_plugin(name="return-test-tool")
    result = ToolRegistry.register(plugin_class)

    assert_that(result).is_equal_to(plugin_class)


# =============================================================================
# Tests for ToolRegistry.get
# =============================================================================


def test_get_registered_tool_returns_instance() -> None:
    """Verify that getting a registered tool returns its instance."""
    tool = ToolRegistry.get("ruff")

    assert_that(tool).is_not_none()
    assert_that(tool).is_instance_of(BaseToolPlugin)
    assert_that(tool.definition.name.lower()).is_equal_to("ruff")


def test_get_unknown_tool_raises_value_error() -> None:
    """Verify that getting an unknown tool raises ValueError with helpful message."""
    with pytest.raises(ValueError, match="Unknown tool"):
        ToolRegistry.get("nonexistent-tool-xyz")


@pytest.mark.parametrize(
    "tool_name_variant",
    [
        "ruff",
        "RUFF",
        "Ruff",
        "RuFf",
    ],
    ids=["lowercase", "uppercase", "capitalized", "mixed_case"],
)
def test_get_is_case_insensitive(tool_name_variant: str) -> None:
    """Verify that tool lookup is case-insensitive.

    Args:
        tool_name_variant: Different case variations of tool names.
    """
    tool = ToolRegistry.get(tool_name_variant)

    assert_that(tool).is_not_none()
    assert_that(tool.definition.name.lower()).is_equal_to("ruff")


# =============================================================================
# Tests for ToolRegistry.get_all
# =============================================================================


def test_get_all_returns_non_empty_dict() -> None:
    """Verify that get_all returns a dictionary with registered tools."""
    all_tools = ToolRegistry.get_all()

    assert_that(all_tools).is_not_empty()
    assert_that("ruff" in all_tools).is_true()


def test_get_all_returns_instances() -> None:
    """Verify that get_all returns tool instances, not classes."""
    all_tools = ToolRegistry.get_all()

    for _name, tool in all_tools.items():
        assert_that(tool).is_instance_of(BaseToolPlugin)


def test_get_all_tools_have_valid_names() -> None:
    """Verify that all returned tools have matching names."""
    all_tools = ToolRegistry.get_all()

    for name, tool in all_tools.items():
        assert_that(tool.definition.name.lower()).is_equal_to(name.lower())


# =============================================================================
# Tests for ToolRegistry.get_definitions
# =============================================================================


def test_get_definitions_returns_non_empty_dict() -> None:
    """Verify that get_definitions returns definitions for all tools."""
    definitions = ToolRegistry.get_definitions()

    assert_that(definitions).is_not_empty()


def test_get_definitions_returns_tool_definitions() -> None:
    """Verify that get_definitions returns ToolDefinition instances."""
    definitions = ToolRegistry.get_definitions()

    for _name, defn in definitions.items():
        assert_that(defn).is_instance_of(ToolDefinition)


@pytest.mark.parametrize(
    "required_field",
    ["name", "description"],
    ids=["has_name", "has_description"],
)
def test_get_definitions_have_required_fields(required_field: str) -> None:
    """Verify that each definition has the required field populated.

    Args:
        required_field: A field that should be present in tool definitions.
    """
    definitions = ToolRegistry.get_definitions()

    for _name, defn in definitions.items():
        value = getattr(defn, required_field)
        assert_that(value).is_not_none()
        if isinstance(value, str):
            assert_that(value).is_not_empty()


# =============================================================================
# Tests for ToolRegistry.get_names
# =============================================================================


def test_get_names_returns_sorted_list() -> None:
    """Verify that get_names returns a sorted list of tool names."""
    names = ToolRegistry.get_names()

    assert_that(names).is_equal_to(sorted(names))


def test_get_names_includes_builtin_tools() -> None:
    """Verify that get_names includes builtin tools like ruff."""
    names = ToolRegistry.get_names()

    assert_that("ruff" in names).is_true()


def test_get_names_returns_list_type() -> None:
    """Verify that get_names returns a list."""
    names = ToolRegistry.get_names()

    assert_that(names).is_instance_of(list)


# =============================================================================
# Tests for ToolRegistry.is_registered
# =============================================================================


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("ruff", True),
        ("RUFF", True),
        ("nonexistent-xyz", False),
        ("", False),
    ],
    ids=[
        "registered_lowercase",
        "registered_uppercase",
        "not_registered",
        "empty_name",
    ],
)
def test_is_registered_returns_correct_boolean(tool_name: str, expected: bool) -> None:
    """Verify that is_registered returns the correct boolean for various inputs.

    Args:
        tool_name: The name of the tool to check.
        expected: The expected result of the check.
    """
    result = ToolRegistry.is_registered(tool_name)

    assert_that(result).is_equal_to(expected)


# =============================================================================
# Tests for ToolRegistry.clear
# =============================================================================


def test_clear_removes_all_tools() -> None:
    """Verify that clear removes all registered tools."""
    # First register some tools
    plugin_class = create_fake_plugin(name="clear-test-tool")
    ToolRegistry.register(plugin_class)
    assert_that(ToolRegistry.is_registered("clear-test-tool")).is_true()

    # Clear and verify
    ToolRegistry.clear()

    assert_that(ToolRegistry._tools).is_empty()
    assert_that(ToolRegistry._instances).is_empty()


def test_clear_results_in_empty_registry(empty_registry: None) -> None:
    """Verify that an empty registry has no tools.

    Args:
        empty_registry: Fixture that provides an empty registry.
    """
    assert_that(ToolRegistry._tools).is_empty()
    assert_that(ToolRegistry._instances).is_empty()


# =============================================================================
# Tests for ToolRegistry.get_check_tools
# =============================================================================


def test_get_check_tools_returns_all_tools() -> None:
    """Verify that get_check_tools returns all tools (all support check)."""
    check_tools = ToolRegistry.get_check_tools()
    all_tools = ToolRegistry.get_all()

    assert_that(check_tools).is_length(len(all_tools))


def test_get_check_tools_returns_instances() -> None:
    """Verify that get_check_tools returns tool instances."""
    check_tools = ToolRegistry.get_check_tools()

    for _name, tool in check_tools.items():
        assert_that(tool).is_instance_of(BaseToolPlugin)


# =============================================================================
# Tests for ToolRegistry.get_fix_tools
# =============================================================================


def test_get_fix_tools_returns_only_fix_capable() -> None:
    """Verify that get_fix_tools returns only tools that can fix."""
    fix_tools = ToolRegistry.get_fix_tools()

    for _name, tool in fix_tools.items():
        assert_that(tool.definition.can_fix).is_true()


def test_get_fix_tools_excludes_non_fix_tools() -> None:
    """Verify that get_fix_tools excludes tools that cannot fix."""
    fix_tools = ToolRegistry.get_fix_tools()
    all_tools = ToolRegistry.get_all()

    non_fix_tools = [
        name for name, tool in all_tools.items() if not tool.definition.can_fix
    ]
    for name in non_fix_tools:
        assert_that(name in fix_tools).is_false()


def test_get_fix_tools_is_subset_of_all_tools() -> None:
    """Verify that fix tools is a subset of all tools."""
    fix_tools = ToolRegistry.get_fix_tools()
    all_tools = ToolRegistry.get_all()

    assert_that(len(fix_tools)).is_less_than_or_equal_to(len(all_tools))
    for name in fix_tools:
        assert_that(name in all_tools).is_true()


# =============================================================================
# Tests for register_tool decorator
# =============================================================================


def test_register_tool_decorator_registers_tool() -> None:
    """Verify that the register_tool decorator registers a tool class."""
    plugin_class = create_fake_plugin(name="decorator-test-tool")
    register_tool(plugin_class)

    assert_that(ToolRegistry.is_registered("decorator-test-tool")).is_true()


def test_register_tool_decorator_returns_class() -> None:
    """Verify that the register_tool decorator returns the class unchanged."""
    plugin_class = create_fake_plugin(name="decorator-return-test")
    result = register_tool(plugin_class)

    assert_that(result).is_equal_to(plugin_class)


def test_register_tool_decorator_can_be_used_as_decorator() -> None:
    """Verify that register_tool works when used as a decorator."""

    @dataclass
    @register_tool
    class DecoratorSyntaxPlugin(BaseToolPlugin):
        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(name="decorator-syntax-test", description="Test")

        def check(self, paths: list[str], options: dict[str, Any]) -> ToolResult:
            return ToolResult(
                name="decorator-syntax-test",
                success=True,
                output="",
                issues_count=0,
            )

    assert_that(ToolRegistry.is_registered("decorator-syntax-test")).is_true()
    tool = ToolRegistry.get("decorator-syntax-test")
    assert_that(tool).is_instance_of(DecoratorSyntaxPlugin)


# =============================================================================
# Tests for edge cases and robustness
# =============================================================================


def test_registry_handles_multiple_registrations() -> None:
    """Verify that registering multiple different tools works correctly."""
    plugin1 = create_fake_plugin(name="multi-test-1")
    plugin2 = create_fake_plugin(name="multi-test-2")
    plugin3 = create_fake_plugin(name="multi-test-3")

    ToolRegistry.register(plugin1)
    ToolRegistry.register(plugin2)
    ToolRegistry.register(plugin3)

    assert_that(ToolRegistry.is_registered("multi-test-1")).is_true()
    assert_that(ToolRegistry.is_registered("multi-test-2")).is_true()
    assert_that(ToolRegistry.is_registered("multi-test-3")).is_true()


def test_get_returns_same_instance_on_multiple_calls() -> None:
    """Verify that get returns the same instance on multiple calls."""
    tool1 = ToolRegistry.get("ruff")
    tool2 = ToolRegistry.get("ruff")

    assert_that(tool1).is_same_as(tool2)
