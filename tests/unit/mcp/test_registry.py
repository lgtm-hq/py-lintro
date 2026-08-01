"""Unit tests for the MCP tool registry."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.mcp.registry import McpToolRegistry, McpToolSpec


def _spec(name: str, *, read_only: bool = True) -> McpToolSpec:
    """Build a minimal tool spec for registry tests.

    Args:
        name: Tool name.
        read_only: Value for the read-only capability flag.

    Returns:
        A valid tool specification.
    """
    return McpToolSpec(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
        handler=lambda _arguments: {"name": name},
        read_only=read_only,
        destructive=False,
        idempotent=True,
    )


def test_registry_register_and_list_preserves_order() -> None:
    """Registry stores tools and lists them in registration order."""
    registry = McpToolRegistry()
    registry.register(spec=_spec("alpha"))
    registry.register_toolkit(specs=[_spec("beta"), _spec("gamma")])

    names = [spec.name for spec in registry.list_tools()]
    assert_that(names).is_equal_to(["alpha", "beta", "gamma"])
    assert_that(len(registry)).is_equal_to(3)
    assert_that("beta" in registry).is_true()
    assert_that(registry.get(name="gamma")).is_not_none()
    assert_that(registry.get(name="missing")).is_none()


def test_registry_rejects_duplicate_names() -> None:
    """Registering the same tool name twice raises ValueError."""
    registry = McpToolRegistry()
    registry.register(spec=_spec("dup"))

    with pytest.raises(ValueError):
        registry.register(spec=_spec("dup"))


def test_spec_rejects_blank_name() -> None:
    """A blank tool name is rejected at construction time."""
    with pytest.raises(ValueError) as exc_info:
        McpToolSpec(
            name="  ",
            description="d",
            input_schema={"type": "object", "properties": {}},
            handler=lambda _arguments: {},
        )

    assert_that(str(exc_info.value)).contains("non-empty")


def test_spec_rejects_non_object_input_schema() -> None:
    """Only JSON Schema object types are accepted as input schemas."""
    with pytest.raises(ValueError) as exc_info:
        McpToolSpec(
            name="bad",
            description="d",
            input_schema={"type": "string"},
            handler=lambda _arguments: {},
        )

    assert_that(str(exc_info.value)).contains("object")


def test_spec_rejects_path_argument_missing_from_schema() -> None:
    """Declared path arguments must exist in the schema properties."""
    with pytest.raises(ValueError) as exc_info:
        McpToolSpec(
            name="bad_paths",
            description="d",
            input_schema={"type": "object", "properties": {"other": {}}},
            handler=lambda _arguments: {},
            path_arguments=("target",),
        )

    assert_that(str(exc_info.value)).contains("target")
