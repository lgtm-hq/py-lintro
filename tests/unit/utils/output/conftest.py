"""Shared fixtures and test data for file writer tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class MockIssue(BaseIssue):
    """Mock issue for testing file writer functionality."""

    file: str = "src/main.py"
    line: int = 10
    code: str = "E001"
    message: str = "Test error"


@pytest.fixture
def mock_tool_result_factory() -> Callable[..., ToolResult]:
    """Provide a factory for creating ToolResult instances."""

    def _create(**kwargs: Any) -> ToolResult:
        return ToolResult(**kwargs)

    return _create


@pytest.fixture
def mock_issue_factory() -> Callable[..., MockIssue]:
    """Provide a factory for creating MockIssue instances."""

    def _create(**kwargs: Any) -> MockIssue:
        return MockIssue(**kwargs)

    return _create


@pytest.fixture
def sample_results_with_issues(
    mock_tool_result_factory: Callable[..., ToolResult],
    mock_issue_factory: Callable[..., MockIssue],
) -> list[ToolResult]:
    """Sample tool results with one issue."""
    return [
        mock_tool_result_factory(
            name="ruff",
            issues_count=1,
            issues=[mock_issue_factory()],
        ),
    ]


@pytest.fixture
def sample_results_empty(
    mock_tool_result_factory: Callable[..., ToolResult],
) -> list[ToolResult]:
    """Sample tool results with no issues."""
    return [mock_tool_result_factory(name="ruff", issues_count=0)]
