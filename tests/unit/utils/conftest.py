"""Pytest configuration for utility unit tests.

Tests in this directory focus on utility functions including:
- ASCII normalization
- Enum and normalizer validation
- Subprocess validator functionality
- Timeout utilities
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class FakeIssue(BaseIssue):
    """Fake Issue for testing output formatters."""

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }

    file: str = "test.py"
    line: int = 1
    column: int = 1
    code: str = "E001"
    message: str = "Test error"
    level: str = "error"
    fixable: bool = False


@pytest.fixture
def tool_result() -> ToolResult:
    """Provide a ToolResult instance for testing.

    Returns:
        ToolResult: Successful result with the canonical field defaults.
    """
    return ToolResult(name="test-tool", success=True, output="")


@pytest.fixture
def tool_result_factory() -> Callable[..., ToolResult]:
    """Factory fixture to create ToolResult with custom attributes.

    Returns:
        Callable[..., ToolResult]: Factory accepting ToolResult field overrides.
    """

    def _create(**kwargs: Any) -> ToolResult:
        defaults = {"name": "test-tool", "success": True, "output": "", **kwargs}
        return ToolResult(**defaults)

    return _create


@pytest.fixture
def fake_issue() -> FakeIssue:
    """Provide a FakeIssue instance for testing.

    Returns:
        FakeIssue: Example lint finding with a file location and rule code.
    """
    return FakeIssue()


@pytest.fixture
def fake_issue_factory() -> Callable[..., FakeIssue]:
    """Factory fixture to create FakeIssue with custom attributes.

    Returns:
        Callable[..., FakeIssue]: Factory accepting example issue field overrides.
    """

    def _create(**kwargs: Any) -> FakeIssue:
        return FakeIssue(**kwargs)

    return _create


@pytest.fixture
def console_capture() -> tuple[Callable[[str], None], list[str]]:
    """Capture console output for testing.

    Returns:
        Tuple containing:
        - capture function that appends text to output list
        - output list containing captured text
    """
    output: list[str] = []

    def capture(text: str = "", **_kwargs: Any) -> None:
        output.append(text)

    return capture, output


@pytest.fixture
def console_capture_with_kwargs() -> tuple[
    Callable[..., None],
    list[tuple[str, dict[str, Any]]],
]:
    """Capture console output with kwargs for testing.

    Returns:
        Tuple containing:
        - capture function that appends (text, kwargs) tuples
        - output list containing captured (text, kwargs) tuples
    """
    output: list[tuple[str, dict[str, Any]]] = []

    def capture(text: str = "", **kwargs: Any) -> None:
        output.append((text, kwargs))

    return capture, output
