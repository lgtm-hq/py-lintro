"""Unit tests for the MCP tool registry."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.mcp.registry import McpToolRegistry, McpToolSpec


def _spec(name: str, *, read_only: bool = True) -> McpToolSpec:
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
    registry.register(_spec("alpha"))
    registry.register_toolkit([_spec("beta"), _spec("gamma")])

    names = [spec.name for spec in registry.list_tools()]
    assert_that(names).is_equal_to(["alpha", "beta", "gamma"])
    assert_that(len(registry)).is_equal_to(3)
    assert_that("beta" in registry).is_true()
    assert_that(registry.get("gamma")).is_not_none()
    assert_that(registry.get("missing")).is_none()


def test_registry_rejects_duplicate_names() -> None:
    """Registering the same tool name twice raises ValueError."""
    registry = McpToolRegistry()
    registry.register(_spec("dup"))

    with pytest.raises(ValueError):
        registry.register(_spec("dup"))
