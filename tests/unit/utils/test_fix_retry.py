"""Tests for fix convergence retry logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.utils.tool_executor import _run_fix_with_retry


@dataclass
class _MockToolDefinition:
    """Minimal mock tool definition.

    Attributes:
        name: Name of the tool.
    """

    name: str = "mock_tool"


@dataclass
class _ConvergingMockTool:
    """Mock tool that converges after a given number of fix passes.

    Attributes:
        definition: Tool definition mock.
        converge_on_pass: Pass number (1-based) on which remaining goes to 0.
    """

    definition: _MockToolDefinition = field(default_factory=_MockToolDefinition)
    converge_on_pass: int = 2
    _call_count: int = field(default=0, init=False)

    def fix(
        self,
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Mock fix that converges after converge_on_pass calls.

        Args:
            paths: Paths to fix.
            options: Fix options.

        Returns:
            ToolResult with remaining issues depending on call count.
        """
        self._call_count += 1
        if self._call_count >= self.converge_on_pass:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="all fixed",
                issues_count=0,
                initial_issues_count=3,
                fixed_issues_count=3,
                remaining_issues_count=0,
            )
        return ToolResult(
            name=self.definition.name,
            success=False,
            output="still has issues",
            issues_count=1,
            initial_issues_count=3,
            fixed_issues_count=2,
            remaining_issues_count=1,
        )


@dataclass
class _NeverConvergingMockTool:
    """Mock tool that never converges.

    Attributes:
        definition: Tool definition mock.
    """

    definition: _MockToolDefinition = field(default_factory=_MockToolDefinition)
    _call_count: int = field(default=0, init=False)

    def fix(
        self,
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Mock fix that always reports remaining issues.

        Args:
            paths: Paths to fix.
            options: Fix options.

        Returns:
            ToolResult with remaining issues.
        """
        self._call_count += 1
        return ToolResult(
            name=self.definition.name,
            success=False,
            output="unfixable",
            issues_count=2,
            initial_issues_count=5,
            fixed_issues_count=3,
            remaining_issues_count=2,
        )


def test_fix_converges_on_second_attempt() -> None:
    """Should succeed when tool converges on the second pass."""
    tool = _ConvergingMockTool(converge_on_pass=2)

    result = _run_fix_with_retry(
        tool=tool,  # type: ignore[arg-type]
        paths=["."],
        options={},
        max_retries=3,
    )

    assert_that(result.success).is_true()
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_equal_to(3)
    assert_that(result.fixed_issues_count).is_equal_to(3)
    assert_that(tool._call_count).is_equal_to(2)


def test_fix_reports_unfixable_after_max_retries() -> None:
    """Should report remaining issues when max retries are exhausted."""
    tool = _NeverConvergingMockTool()

    result = _run_fix_with_retry(
        tool=tool,  # type: ignore[arg-type]
        paths=["."],
        options={},
        max_retries=3,
    )

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(2)
    assert_that(tool._call_count).is_equal_to(3)


def test_fix_no_retry_when_first_pass_succeeds() -> None:
    """Should not retry when the first pass succeeds."""
    tool = _ConvergingMockTool(converge_on_pass=1)

    result = _run_fix_with_retry(
        tool=tool,  # type: ignore[arg-type]
        paths=["."],
        options={},
        max_retries=3,
    )

    assert_that(result.success).is_true()
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(tool._call_count).is_equal_to(1)


def test_fix_retry_merges_results_correctly() -> None:
    """Should keep initial count from first pass and remaining from last."""
    tool = _ConvergingMockTool(converge_on_pass=3)

    result = _run_fix_with_retry(
        tool=tool,  # type: ignore[arg-type]
        paths=["."],
        options={},
        max_retries=5,
    )

    # initial_issues_count should be from the first pass (3)
    assert_that(result.initial_issues_count).is_equal_to(3)
    # remaining from last pass (0 since it converged on pass 3)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    # fixed = initial - remaining = 3 - 0 = 3
    assert_that(result.fixed_issues_count).is_equal_to(3)
    assert_that(tool._call_count).is_equal_to(3)
