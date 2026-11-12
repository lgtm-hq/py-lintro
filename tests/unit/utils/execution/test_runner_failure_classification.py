"""npm failing before a tool runs is an execution failure, not a verdict (#2043).

An npx fallback can be rejected by npm itself, for example when an npmrc
``workspace=<name>`` conflicts with ``--workspaces=false``. The tool then never
reports, so the result must not read as findings or as a pass.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.models.core.tool_outcome import (
    Checked,
    NoFiles,
    Skipped,
    TimedOut,
    ToolOutcome,
    Unavailable,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.execution.result_shaping import classify_runner_failure

_NPM_CONFLICT = (
    "npm error Cannot use --no-workspaces and --workspace at the same time\n"
    "npm error A complete log of this run can be found in: /tmp/x.log\n"
)


def _failed(output: str, *, issues: int = 0) -> ToolResult:
    """Build a failed check result with *issues* findings and *output*.

    Args:
        output: Raw tool output.
        issues: Number of findings to attach.

    Returns:
        The result.
    """
    findings = [
        BaseIssue(file=f"f{i}.json", line=1, message="m") for i in range(issues)
    ]
    return ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=False), issues_count=issues),
        name="prettier",
        output=output,
        issues=findings,
    )


@pytest.mark.parametrize(
    "output",
    [_NPM_CONFLICT, "npm ERR! code ENOWORKSPACES\nnpm ERR! no workspaces\n"],
    ids=["npm-error", "legacy-npm-err"],
)
def test_npm_error_without_a_report_is_an_execution_failure(output: str) -> None:
    """Npm's own error with no findings marks the run as failed to execute.

    Args:
        output: npm's failure output.
    """
    result = classify_runner_failure(_failed(output))

    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.output).starts_with(
        "npm failed before the tool produced a report:",
    ).contains(output.splitlines()[0])


@pytest.mark.parametrize(
    ("result", "case"),
    [
        (_failed("[warn] data.json\n"), "tool-failure-without-npm"),
        (_failed(_NPM_CONFLICT, issues=1), "has-findings"),
        (
            ToolResult.from_outcome(
                ToolOutcome(execution=Checked(success=True)),
                name="prettier",
                output=_NPM_CONFLICT,
            ),
            "succeeded",
        ),
        (
            ToolResult.from_outcome(
                ToolOutcome(execution=Skipped("version below minimum")),
                name="prettier",
                output=_NPM_CONFLICT,
            ),
            "skipped",
        ),
        (
            ToolResult.from_outcome(
                ToolOutcome(execution=Unavailable(reason="npx only")),
                name="prettier",
                output=_NPM_CONFLICT,
            ),
            "unavailable",
        ),
        (
            ToolResult.from_outcome(
                ToolOutcome(execution=TimedOut(_NPM_CONFLICT)),
                name="prettier",
                output=_NPM_CONFLICT,
            ),
            "timed-out",
        ),
        (
            ToolResult.from_outcome(
                ToolOutcome(execution=NoFiles()),
                name="prettier",
                output=_NPM_CONFLICT,
            ),
            "no-files",
        ),
        (_failed("prettier: npm error mentioned mid-line\n"), "not-npm-line"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_other_results_are_left_unchanged(result: ToolResult, case: str) -> None:
    """Only a findings-free failure carrying npm's own error is reclassified.

    Args:
        result: Result that must pass through untouched.
        case: Scenario name.
    """
    del case

    assert_that(classify_runner_failure(result)).is_equal_to(result)
