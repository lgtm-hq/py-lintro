"""Tests for fix convergence retry logic."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.execution.exit_codes import aggregate_tool_results
from lintro.utils.execution.fix_retry import run_fix_with_retry
from lintro.utils.json_output import serialize_tool_result
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


@pytest.mark.parametrize("initial_count", [None, 4])
@pytest.mark.parametrize("failure_kind", ["parse", "unknown"])
@pytest.mark.parametrize("parsed_count", [0, 1])
def test_retry_preserves_count_only_accounting(
    initial_count: int | None,
    failure_kind: str,
    parsed_count: int,
) -> None:
    """An incomplete retry must retain counts beyond its parsed findings.

    Args:
        initial_count: First-pass measurement, if one was available.
        failure_kind: Whether parsing or execution left the residual unknown.
        parsed_count: Number of usable findings in the incomplete report.
    """
    partial = [BaseIssue(file="partial.txt", message="remaining")][:parsed_count]
    tool = MagicMock()
    tool.fix.side_effect = [
        ToolResult(
            name="formatter",
            issues_count=4,
            initial_issues_count=initial_count,
        ),
        ToolResult(
            name="formatter",
            issues_count=7,
            issues=partial,
            parse_failures_count=1 if failure_kind == "parse" else None,
            residual_unknown=failure_kind == "unknown",
            residual_unknown_reason=(
                "verification unavailable" if failure_kind == "unknown" else None
            ),
        ),
    ]

    result = run_fix_with_retry(tool, ["."], {}, max_retries=3)

    assert_that(result.issues_count).is_equal_to(7)
    assert_that(result.issues).is_equal_to(partial)
    assert_that(result.initial_issues_count).is_equal_to(initial_count)
    assert_that(result.initial_issues).is_empty()
    assert_that(result.success).is_false()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((7, 0, 0))


@pytest.mark.parametrize("initial_count", [None, 0])
@pytest.mark.parametrize("failure_kind", ["parse", "unknown"])
def test_retry_distinguishes_measured_zero_from_unmeasured_initial_issues(
    initial_count: int | None,
    failure_kind: str,
) -> None:
    """An empty detected list is measured only when its count says so.

    Args:
        initial_count: An explicit measured zero or an unavailable baseline.
        failure_kind: Whether the latest findings came from a partial report.
    """
    latest = [BaseIssue(file="new.txt", message="finding introduced after fixing")]
    tool = MagicMock()
    tool.fix.side_effect = [
        ToolResult(
            name="formatter",
            issues_count=1,
            initial_issues_count=initial_count,
        ),
        ToolResult(
            name="formatter",
            issues_count=1,
            issues=latest,
            initial_issues_count=1,
            initial_issues=latest,
            parse_failures_count=1 if failure_kind == "parse" else None,
            residual_unknown=failure_kind == "unknown",
            residual_unknown_reason=(
                "verification unavailable" if failure_kind == "unknown" else None
            ),
        ),
    ]

    result = run_fix_with_retry(tool, ["."], {}, max_retries=3)

    assert_that(result.initial_issues_count).is_equal_to(initial_count)
    assert_that(result.initial_issues).is_empty()
    assert_that(result.issues).is_equal_to(latest)
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.success).is_false()
    assert_that(result.residual_unknown).is_true()

    payload = serialize_tool_result(result, action=Action.FIX)
    assert_that(payload["issues_count"]).is_equal_to(result.issues_count)
    assert_that(payload["issues"][0]["file"]).is_equal_to("new.txt")
    assert_that(payload["fixed"]).is_none()
    assert_that(payload["remaining"]).is_none()


@pytest.mark.parametrize("initial_count", [None, 3])
@pytest.mark.parametrize("timed_out", [False, True])
def test_fix_retry_merge_preserves_last_pass_payload(
    initial_count: int | None,
    timed_out: bool,
) -> None:
    """Preserve last-pass payloads with explicit or inferred first-pass counts.

    Args:
        initial_count: First-pass measurement, if one was available.
        timed_out: Whether the final pass exceeded its execution deadline.
    """
    detected = [
        BaseIssue(file="a.txt", message="format a"),
        BaseIssue(file="b.txt", message="format b"),
        BaseIssue(file="c.txt", message="format c"),
    ]
    first = ToolResult(
        name="formatter",
        success=False,
        issues_count=2,
        initial_issues_count=initial_count,
        initial_issues=detected,
        metadata={"stage": "first"},
        pytest_summary={"passed": 0},
    )
    last = ToolResult(
        name="formatter",
        success=False,
        output="last pass",
        formatted_output="last formatted output",
        issues_count=1,
        issues=detected[-1:],
        initial_issues_count=2,
        fixed_issues_count=1,
        remaining_issues_count=1,
        initial_issues=detected[-2:],
        metadata={"suppressions": [{"id": "GHSA-example"}]},
        parse_failures_count=0,
        pytest_summary={"passed": 2, "failed": 1},
        timed_out=timed_out,
        inspected_files=False if timed_out else None,
    )
    tool = MagicMock()
    tool.fix.side_effect = [first, last]

    result = run_fix_with_retry(tool, ["."], {}, max_retries=2)

    assert_that(result.initial_issues_count).is_equal_to(3)
    assert_that(result.initial_issues).is_equal_to(detected)
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(2)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.issues).is_equal_to(last.issues)
    assert_that(result.success).is_false()
    assert_that(result.output).is_equal_to("last pass")
    assert_that(result.formatted_output).is_equal_to("last formatted output")
    assert_that(result.metadata).is_equal_to(last.metadata)
    assert_that(result.parse_failures_count).is_equal_to(0)
    assert_that(result.pytest_summary).is_equal_to(last.pytest_summary)
    assert_that(result.timed_out).is_equal_to(timed_out)


@pytest.mark.parametrize("failure_pass", [1, 2, 3])
@pytest.mark.parametrize("initial_count", [None, 1])
@pytest.mark.parametrize("failure_kind", ["parse", "unknown"])
def test_incomplete_fix_pass_is_preserved_without_more_retries(
    failure_pass: int,
    initial_count: int | None,
    failure_kind: str,
) -> None:
    """Keep the first complete baseline and the latest incomplete evidence."""
    detected = [
        BaseIssue(file="a.txt", message="format a"),
        BaseIssue(file="b.txt", message="format b"),
        BaseIssue(file="c.txt", message="format c"),
    ]
    partial_findings = [BaseIssue(file="d.txt", message="partial verification")]
    incomplete = ToolResult(
        name="formatter",
        success=False,
        output="incomplete report",
        issues_count=1,
        issues=partial_findings,
        initial_issues=detected[-1:],
        initial_issues_count=initial_count,
        parse_failures_count=1 if failure_kind == "parse" else None,
        residual_unknown=failure_kind == "unknown",
        residual_unknown_reason=(
            "verification unavailable" if failure_kind == "unknown" else None
        ),
        timed_out=True,
        cwd="/repo",
        duration_seconds=1.5,
        metadata={"stage": "incomplete"},
        pytest_summary={"passed": 1, "errors": 1},
        inspected_files=False,
    )
    measured = ToolResult(
        name="formatter",
        success=False,
        issues_count=1,
        issues=detected[-1:],
        initial_issues_count=3,
        fixed_issues_count=2,
        remaining_issues_count=1,
        initial_issues=detected,
    )
    tool = MagicMock()
    tool.fix.side_effect = [measured] * (failure_pass - 1) + [incomplete]

    result = run_fix_with_retry(tool, ["."], {}, max_retries=3)

    assert_that(incomplete.residual_unknown).is_equal_to(failure_kind == "unknown")
    if failure_pass == 1:
        assert_that(result is incomplete).is_equal_to(failure_kind == "unknown")
        assert_that(result.initial_issues_count).is_equal_to(initial_count)
        assert_that(result.initial_issues).is_equal_to(detected[-1:])
    else:
        assert_that(result.initial_issues_count).is_equal_to(3)
        assert_that(result.initial_issues).is_equal_to(detected)
    assert_that(result.success).is_false()
    assert_that(result.output).is_equal_to("incomplete report")
    expected_findings = (
        detected if failure_pass > 1 and failure_kind == "unknown" else partial_findings
    )
    assert_that(result.issues).is_equal_to(expected_findings)
    assert_that(result.issues_count).is_equal_to(len(expected_findings))
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.parse_failures_count).is_equal_to(
        1 if failure_kind == "parse" else None,
    )
    assert_that(result.residual_unknown).is_true()
    assert_that(result.residual_unknown_reason).is_equal_to(
        (
            "verification unavailable"
            if failure_kind == "unknown"
            else "tool output could not be fully parsed"
        ),
    )
    assert_that(result.timed_out).is_true()
    assert_that(result.cwd).is_equal_to("/repo")
    assert_that(result.duration_seconds).is_equal_to(1.5)
    assert_that(result.metadata).is_equal_to({"stage": "incomplete"})
    assert_that(result.pytest_summary).is_equal_to({"passed": 1, "errors": 1})
    assert_that(replace(result)).is_equal_to(result)
