"""Pytest configuration for utility unit tests.

Tests in this directory focus on utility functions including:
- ASCII normalization
- Enum and normalizer validation
- Subprocess validator functionality
- Timeout utilities
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class FakeToolResult:
    """Fake ToolResult for testing console/logger and summary output."""

    name: str = "test-tool"
    success: bool = True
    issues_count: int = 0
    output: str = ""
    issues: list[Any] = field(default_factory=list)
    fixed_issues_count: int | None = None
    remaining_issues_count: int | None = None
    pytest_summary: dict[str, Any] | None = None
    ai_metadata: dict[str, Any] | None = None


@dataclass
class FakeIssue:
    """Fake Issue for testing output formatters."""

    file: str = "test.py"
    line: int = 1
    column: int = 1
    code: str = "E001"
    message: str = "Test error"
    level: str = "error"


@pytest.fixture
def fake_tool_result() -> FakeToolResult:
    """Provide a FakeToolResult instance for testing.

    Returns:
        FakeToolResult: Description of returned FakeToolResult.
    """
    return FakeToolResult()


@pytest.fixture
def fake_tool_result_factory() -> Callable[..., FakeToolResult]:
    """Factory fixture to create FakeToolResult with custom attributes.

    Returns:
        Callable[..., FakeToolResult]: Description of returned Callable[..., FakeToolResult].
    """

    def _create(**kwargs: Any) -> FakeToolResult:
        return FakeToolResult(**kwargs)

    return _create


@pytest.fixture
def fake_issue() -> FakeIssue:
    """Provide a FakeIssue instance for testing.

    Returns:
        FakeIssue: Description of returned FakeIssue.
    """
    return FakeIssue()


@pytest.fixture
def fake_issue_factory() -> Callable[..., FakeIssue]:
    """Factory fixture to create FakeIssue with custom attributes.

    Returns:
        Callable[..., FakeIssue]: Description of returned Callable[..., FakeIssue].
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
def console_capture_with_kwargs() -> (
    tuple[Callable[..., None], list[tuple[str, dict[str, Any]]]]
):
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
