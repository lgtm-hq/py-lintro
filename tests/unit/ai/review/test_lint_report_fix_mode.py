"""A saved fix-mode report round-trips without a false verdict (#3497).

``lintro fmt --output-format json`` writes each tool's ``issues`` as the
findings detected before the fix merged with the ones left after it, and
carries the verdict in ``remaining``. Read back through ``--lint-report``, a
clean fix must not read as incomplete, and findings the fix removed must not
reach the review prompt as current ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.lint_bridge import (
    LintReportIssue,
    format_lint_results_for_prompt,
    load_lint_report,
    restrict_lint_results_to_files,
)
from lintro.ai.review.lint_completeness import count_lint_results
from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.models.core.tool_outcome import Checked, FixUnmeasured, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.utils.json_output import serialize_tool_result

_CHANGED = "src/app.py"


def _issue(message: str, line: int) -> LintReportIssue:
    """Build a finding on the changed file.

    Args:
        message: Its message.
        line: Its line.

    Returns:
        The finding.
    """
    return LintReportIssue(file=_CHANGED, line=line, message=message, code="X1")


_A = _issue("fixed finding", 1)
_B = _issue("remaining finding", 2)


def _fix(*, issues: list[LintReportIssue], fixed: int, success: bool) -> ToolResult:
    """Build a measured fix that saw A and B before it ran.

    Args:
        issues: The findings left after the fix.
        fixed: How many it fixed.
        success: Its verdict.

    Returns:
        The fix result.
    """
    return ToolResult(
        name="ruff",
        success=success,
        issues=issues,
        issues_count=len(issues),
        initial_issues=[_A, _B],
        initial_issues_count=2,
        fixed_issues_count=fixed,
        remaining_issues_count=len(issues),
        capability=Cap.FIX,
    )


@pytest.mark.parametrize(
    ("result", "counts", "notes", "findings"),
    [
        pytest.param(
            _fix(issues=[], fixed=2, success=True),
            (1, 0),
            [],
            [],
            id="all-fixed",
        ),
        pytest.param(
            _fix(issues=[_B], fixed=1, success=False),
            (1, 0),
            [
                "Tool: ruff | fix report: 1 issue(s) remain after the fix; "
                "the report does not say which",
            ],
            [],
            id="some-remaining",
        ),
        pytest.param(
            _fix(issues=[_A, _B], fixed=0, success=False),
            (1, 0),
            [],
            ["fixed finding", "remaining finding"],
            id="none-fixed",
        ),
        pytest.param(
            ToolResult(
                name="ruff",
                success=False,
                output="Tool execution failed: RuntimeError: boom",
                execution_failed=True,
                residual_unknown=True,
                residual_unknown_reason="tool crashed",
                initial_issues=[_A],
                initial_issues_count=1,
                capability=Cap.FIX,
            ),
            (0, 1),
            [
                "Tool: ruff | did not run / incomplete: Tool execution failed: "
                "RuntimeError: boom",
            ],
            [],
            id="fix-failed",
        ),
        pytest.param(
            ToolResult.from_outcome(
                ToolOutcome(
                    execution=Checked(success=False),
                    accounting=FixUnmeasured(reason="ruff fix crashed", initial=2),
                    issues_count=2,
                ),
                name="ruff",
                issues=[_A, _B],
                capability=Cap.FIX,
            ),
            (0, 1),
            [
                "Tool: ruff | did not run / incomplete: remaining issues unknown "
                "(ruff fix crashed)",
            ],
            [],
            id="residual-unknown",
        ),
    ],
)
def test_fix_report_round_trip_reads_the_remaining_count(
    tmp_path: Path,
    result: ToolResult,
    counts: tuple[int, int],
    notes: list[str],
    findings: list[str],
) -> None:
    """Each fix shape the real writer produces keeps its true verdict.

    Args:
        tmp_path: Pytest temporary directory.
        result: The live fix result.
        counts: Expected tools ran and incomplete.
        notes: Expected tool notes in the digest.
        findings: Messages of the findings the prompt may show as current.
    """
    report = tmp_path / "results.json"
    entry = serialize_tool_result(result, action=Action.FIX)
    report.write_text(json.dumps({"results": [entry]}), encoding="utf-8")

    reloaded = restrict_lint_results_to_files(
        results=load_lint_report(report),
        changed_files=[_CHANGED],
    )
    digest = format_lint_results_for_prompt(results=reloaded)
    tallies = count_lint_results(reloaded)

    assert_that((tallies.ran, tallies.incomplete)).is_equal_to(counts)
    assert_that(
        [
            line
            for line in digest.splitlines()
            if line.startswith("Tool: ") and " | file: " not in line
        ],
    ).is_equal_to(notes)
    assert_that(
        [
            line.split("| Message: ", 1)[1]
            for line in digest.splitlines()
            if "| Message: " in line
        ],
    ).is_equal_to(findings)
