"""An incomplete verify CHECK is no measured residual (#3497, #3498).

A verify check that refused a command, or crashed on part of its scope,
still reports the findings of the invocations that finished. They are real
but partial: the pass must keep them, report the residual as unknown, and
never fold the stale pre-fix findings in as a measured after-count.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from assertpy import assert_that

import lintro.tools as lintro_tools
from lintro.enums.capability import Cap
from lintro.enums.verify_status import VerifyStatus
from lintro.models.core.claim import Claim
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.tools.core.incomplete_run import incomplete_run_result
from lintro.tools.core.verify_pass import (
    VerifyOutcome,
    VerifyScope,
    fold_verify_results,
    run_verify_pass,
)

if TYPE_CHECKING:
    from lintro.tools.core.verify_pass import VerifiableTool

_TOOL = "ruff"
_A = "/repo/a.py"
_B = "/repo/b.py"

#: A Node shim with an argument ``cmd.exe`` would interpret; lintro refuses it.
_REFUSED_COMMAND: tuple[str, ...] = (
    "C:/repo/node_modules/.bin/prettier.cmd",
    "a&whoami&b",
)


def _finding(path: str, line: int, message: str) -> BaseIssue:
    """Build a finding at a file and line.

    Args:
        path: File the finding names.
        line: Line it names.
        message: Its message.

    Returns:
        The finding.
    """
    return BaseIssue(file=path, line=line, column=1, message=message)


@dataclass
class _RefusingCheck(BaseToolPlugin):
    """Verify check that keeps one finding and swallows a refused command."""

    @property
    def definition(self) -> ToolDefinition:
        """Return metadata for the in-memory plugin.

        Returns:
            A check-only tool definition.
        """
        return ToolDefinition(name=_TOOL, description="Refused verify check")

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Report the finding from a.py, then fail the refused invocation.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            An ordinary failed verdict carrying the finding, as several real
            plugins report a launch ``ValueError``.
        """
        try:
            self._run_subprocess(list(_REFUSED_COMMAND))
        except ValueError as exc:
            return ToolResult.from_outcome(
                ToolOutcome(execution=Checked(success=False), issues_count=1),
                name=_TOOL,
                issues=[_finding(_A, 7, "new")],
                output=f"Execution failed: {exc}",
            )
        return ToolResult(name=_TOOL, success=True)

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Unused fix entry point.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            The check result.
        """
        return self.check(paths, options)


def _fixed_mutation() -> ToolResult:
    """Build a fix that saw A (a.py) and B (b.py) and reported both fixed.

    Returns:
        The mutation-phase result.
    """
    return ToolResult(
        name=_TOOL,
        success=True,
        issues=[],
        issues_count=0,
        initial_issues=[_finding(_A, 1, "A"), _finding(_B, 1, "B")],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
        capability=Cap.FIX,
    )


def _fold(outcome: VerifyOutcome) -> ToolResult:
    """Fold one verify outcome into the fixed mutation.

    Args:
        outcome: The verify pass's outcome for the tool.

    Returns:
        The folded result.
    """
    results = [_fixed_mutation()]
    fold_verify_results(
        mutation_results=results,
        verify_results=[outcome],
        scope=VerifyScope(files=(_A, _B), narrowed=True),
    )
    return results[0]


def test_failed_verify_check_keeps_its_findings_and_leaves_residual_unknown() -> None:
    """A check that crashed on b.py measures nothing over the scope.

    It reports one real new finding in a.py. Counting the pre-fix findings
    as survivors would report ``remaining=2, fixed=0`` that no run measured
    and drop the new finding.
    """
    verify = incomplete_run_result(
        name=_TOOL,
        reason="ruff crashed on b.py",
        issues=[_finding(_A, 7, "new")],
    )

    folded = _fold(VerifyOutcome(tool=_TOOL, result=verify))

    assert_that(folded.residual_unknown).is_true()
    assert_that(folded.residual_unknown_reason).contains("ruff crashed on b.py")
    assert_that(folded.remaining_issues_count).is_none()
    assert_that(folded.fixed_issues_count).is_none()
    assert_that([issue.message for issue in folded.issues]).contains("new")
    assert_that(folded.success).is_false()


def test_refused_verify_check_is_unknown_and_never_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal the plugin swallows never yields a VERIFIED residual.

    Args:
        monkeypatch: Pytest patch fixture.
    """
    # The registry entry only has to declare the claims that make the tool
    # a verifying one; the configured copy is what runs.
    registered = SimpleNamespace(
        definition=SimpleNamespace(
            claims=[Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK})],
        ),
    )
    monkeypatch.setattr(
        lintro_tools,
        "tool_manager",
        SimpleNamespace(get_tool={_TOOL: registered}.__getitem__),
    )
    check = _RefusingCheck()

    outcomes = run_verify_pass(
        tools_to_run=[_TOOL],
        scope=VerifyScope(files=(_A, _B), narrowed=True),
        configure=lambda *, tool_name: cast("VerifiableTool", check),
    )

    assert_that(outcomes).is_length(1)
    outcome = outcomes[0]
    assert_that(outcome.status).is_equal_to(VerifyStatus.UNKNOWN)
    assert_that(outcome.unknown_reason).contains(f"lintro refused to run {_TOOL}")
    folded = _fold(outcome)
    assert_that(folded.residual_unknown).is_true()
    assert_that(folded.remaining_issues_count).is_none()
    assert_that(folded.fixed_issues_count).is_none()
    assert_that(folded.execution_failed).is_true()
    assert_that([issue.message for issue in folded.issues]).contains("new")
