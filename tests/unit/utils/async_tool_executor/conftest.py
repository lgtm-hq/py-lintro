"""Shared fixtures for AsyncToolExecutor tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.utils.async_tool_executor import AsyncToolExecutor


@dataclass
class MockToolDefinition:
    """Mock tool definition for testing.

    Attributes:
        name: Name of the tool.
        conflicts_with: List of tools this tool conflicts with.
    """

    name: str = "mock_tool"
    conflicts_with: list[str] = field(default_factory=list)


@dataclass
class MockToolPlugin:
    """Mock tool plugin for testing.

    Attributes:
        definition: Tool definition mock.
        check_result: Result to return from check().
        fix_result: Result to return from fix().
        check_called: Whether check() was called.
        fix_called: Whether fix() was called.
        delay: Optional delay in seconds before returning result.
    """

    definition: MockToolDefinition = field(default_factory=MockToolDefinition)
    check_result: ToolResult | None = None
    fix_result: ToolResult | None = None
    check_called: bool = field(default=False, init=False)
    fix_called: bool = field(default=False, init=False)
    delay: float = 0.0

    def __post_init__(self) -> None:
        """Initialize default results if not provided."""
        if self.check_result is None:
            self.check_result = ToolResult(
                name=self.definition.name,
                success=True,
                output="check output",
                issues_count=0,
            )
        if self.fix_result is None:
            self.fix_result = ToolResult(
                name=self.definition.name,
                success=True,
                output="fix output",
                issues_count=0,
            )

    def check(
        self,
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Mock check method.

        Args:
            paths: Paths to check.
            options: Check options.

        Returns:
            ToolResult: The configured check result.
        """
        self.check_called = True
        if self.delay > 0:
            import time

            time.sleep(self.delay)
        # check_result is guaranteed non-None by __post_init__
        assert_that(self.check_result).is_not_none()
        assert self.check_result is not None  # narrow type for mypy
        return self.check_result

    def fix(
        self,
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Mock fix method.

        Args:
            paths: Paths to fix.
            options: Fix options.

        Returns:
            ToolResult: The configured fix result.
        """
        self.fix_called = True
        if self.delay > 0:
            import time

            time.sleep(self.delay)
        # fix_result is guaranteed non-None by __post_init__
        assert_that(self.fix_result).is_not_none()
        assert self.fix_result is not None  # narrow type for mypy
        return self.fix_result


@pytest.fixture
def executor() -> Iterator[AsyncToolExecutor]:
    """Create an AsyncToolExecutor for testing.

    Yields:
        AsyncToolExecutor: The executor instance.
    """
    exec_instance = AsyncToolExecutor(max_workers=2)
    yield exec_instance
    exec_instance.shutdown()
