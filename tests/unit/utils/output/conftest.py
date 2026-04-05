"""Shared fixtures and test data for file writer tests.

Provides MockIssue and MockToolResult dataclasses along with factories
for creating test data across multiple file writer test modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class MockIssue:
    """Mock issue for testing file writer functionality."""

    file: str = "src/main.py"
    line: int = 10
    code: str = "E001"
    message: str = "Test error"


@dataclass
class MockToolResult:
    """Mock tool result for testing file writer functionality."""

    name: str = "test-tool"
    success: bool = True
    issues_count: int = 0
    output: str = ""
    issues: list[MockIssue] = field(default_factory=list)
    initial_issues: list[MockIssue] | None = None


@pytest.fixture
def mock_tool_result_factory() -> Callable[..., MockToolResult]:
    """Provide a factory for creating MockToolResult instances with custom attributes.

    Returns:
        Factory function that creates MockToolResult instances.
    """

    def _create(**kwargs: Any) -> MockToolResult:
        return MockToolResult(**kwargs)

    return _create


@pytest.fixture
def mock_issue_factory() -> Callable[..., MockIssue]:
    """Provide a factory for creating MockIssue instances with custom attributes.

    Returns:
        Factory function that creates MockIssue instances.
    """

    def _create(**kwargs: Any) -> MockIssue:
        return MockIssue(**kwargs)

    return _create


@pytest.fixture
def sample_results_with_issues(
    mock_tool_result_factory: Callable[..., MockToolResult],
    mock_issue_factory: Callable[..., MockIssue],
) -> list[MockToolResult]:
    """Provide sample tool results with issues for testing output formats.

    Args:
        mock_tool_result_factory: Factory for creating mock tool results.
        mock_issue_factory: Factory for creating mock issues.

    Returns:
        List containing a single MockToolResult with one issue.
    """
    return [
        mock_tool_result_factory(
            name="ruff",
            issues_count=1,
            issues=[mock_issue_factory()],
        ),
    ]


@pytest.fixture
def sample_results_empty(
    mock_tool_result_factory: Callable[..., MockToolResult],
) -> list[MockToolResult]:
    """Provide sample tool results with no issues for testing output formats.

    Args:
        mock_tool_result_factory: Factory for creating mock tool results.

    Returns:
        List containing a single MockToolResult with zero issues.
    """
    return [mock_tool_result_factory(name="ruff", issues_count=0)]
