"""Pytest configuration for plugin unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.discovery import reset_discovery
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Generator


@dataclass
class FakeToolPlugin(BaseToolPlugin):
    """Fake tool plugin for testing."""

    _definition: ToolDefinition = field(
        default_factory=lambda: ToolDefinition(
            name="fake-tool",
            description="Fake tool for testing",
            file_patterns=["*.py"],
            can_fix=True,
            default_timeout=30,
        ),
    )

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            The tool definition.
        """
        return self._definition

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fake check implementation.

        Args:
            paths: List of file paths to check.
            options: Dictionary of options for the check.

        Returns:
            A ToolResult indicating success.
        """
        return ToolResult(name="fake-tool", success=True, output="OK", issues_count=0)


@pytest.fixture
def fake_tool_plugin() -> FakeToolPlugin:
    """Provide a FakeToolPlugin instance for testing.

    Returns:
        A FakeToolPlugin instance.
    """
    return FakeToolPlugin()


@pytest.fixture
def empty_registry() -> None:
    """Clear the plugin registry for the duration of one test.

    The root ``_isolate_plugin_registry`` autouse fixture restores the registry
    afterwards (#2315), so this fixture only has to empty it.
    """
    ToolRegistry.clear()


@pytest.fixture
def reset_discovery_state() -> Generator[None]:
    """Reset discovery state before and after test.

    Yields:
        None: Resets discovery state.
    """
    reset_discovery()
    try:
        yield
    finally:
        reset_discovery()


def create_fake_plugin(
    name: str = "fake-tool",
    description: str = "Fake tool for testing",
    file_patterns: list[str] | None = None,
    can_fix: bool = True,
) -> type[BaseToolPlugin]:
    """Factory function to create fake plugin classes with custom attributes.

    Args:
        name: Tool name.
        description: Tool description.
        file_patterns: File patterns the tool handles.
        can_fix: Whether the tool can fix issues.

    Returns:
        A new FakePlugin class with the specified attributes.
    """
    patterns: list[str] = file_patterns if file_patterns is not None else ["*.py"]

    @dataclass
    class DynamicFakePlugin(BaseToolPlugin):
        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description=description,
                file_patterns=patterns,
                can_fix=can_fix,
            )

        def check(self, paths: list[str], options: dict[str, Any]) -> ToolResult:
            return ToolResult(name=name, success=True, output="OK", issues_count=0)

    return DynamicFakePlugin
