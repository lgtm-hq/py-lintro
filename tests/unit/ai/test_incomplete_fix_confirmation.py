"""Incomplete re-run results never confirm or reject an AI fix (#3498).

A re-run that refused a command, or one that failed part-way and kept only
the findings of the invocations that finished, carries partial findings.
Absent findings there prove nothing, so neither post-fix validation nor the
re-run that refreshes counts may treat such a result as an answer. Both run
the check on a per-run copy, so a refusal never sticks on the registry
singleton.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion
from lintro.ai.rerun import rerun_tools
from lintro.ai.validation import validate_applied_fixes, verify_fixes
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.tools import tool_manager

from .conftest import MockIssue

_TOOL = "partial-tool"

#: A Node shim with an argument ``cmd.exe`` would interpret; lintro refuses it.
_REFUSED_COMMAND: tuple[str, ...] = (
    "C:/repo/node_modules/.bin/prettier.cmd",
    "a&whoami&b",
)


@dataclass
class _PartialPlugin(BaseToolPlugin):
    """Plugin whose check keeps one finding from a run that did not complete.

    ``mode`` selects how the run is incomplete: ``"refused"`` swallows a
    refused command into an ordinary failed verdict, as several real plugins
    do, and ``"execution_failed"`` reports the failure itself the way a
    multi-file run with one crashed invocation does.
    """

    mode: str = "refused"
    finding_file: str = ""

    @property
    def definition(self) -> ToolDefinition:
        """Return metadata for the in-memory plugin.

        Returns:
            A check-only tool definition.
        """
        return ToolDefinition(name=_TOOL, description="Partial result test")

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Report the one finding the completed invocation produced.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            A failed verdict carrying the partial finding.
        """
        finding = MockIssue(file=self.finding_file, line=1, code="E1")
        if self.mode == "refused":
            try:
                self._run_subprocess(list(_REFUSED_COMMAND))
            except ValueError as exc:
                return ToolResult.from_outcome(
                    ToolOutcome(execution=Checked(success=False), issues_count=1),
                    name=_TOOL,
                    issues=[finding],
                    output=f"Execution failed: {exc}",
                )
        return ToolResult(
            name=_TOOL,
            success=False,
            issues=[finding],
            issues_count=1,
            execution_failed=True,
            findings_complete=False,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Unused fix entry point.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            The check result.
        """
        return self.check(paths, options)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
    source: Path,
) -> _PartialPlugin:
    """Register a partial plugin as the registry's only instance of the tool.

    Args:
        monkeypatch: Pytest patch fixture.
        mode: How the plugin's run is incomplete.
        source: File the partial finding names.

    Returns:
        The registry singleton that ``get_tool`` hands out.
    """
    singleton = _PartialPlugin(mode=mode, finding_file=str(source))
    monkeypatch.setattr(tool_manager, "get_tool", {_TOOL: singleton}.__getitem__)
    return singleton


def _source(tmp_path: Path) -> Path:
    """Write the file the AI fix changed.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The resolved file path.
    """
    source = tmp_path / "main.py"
    source.write_text("x = 1\n", encoding="utf-8")
    return source.resolve()


def _fixed_suggestion(source: Path) -> AIFixSuggestion:
    """Build an applied fix whose issue is absent from the partial findings.

    Args:
        source: File the fix changed.

    Returns:
        A suggestion for code ``E2``, which the partial run does not report.
    """
    return AIFixSuggestion(
        file=str(source),
        line=5,
        code="E2",
        tool_name=_TOOL,
        original_code="x = 1",
        suggested_code="x = 2",
        explanation="Fix E2",
    )


_INCOMPLETE_MODES = [
    pytest.param("refused", id="refused-with-partial-findings"),
    pytest.param("execution_failed", id="failed-run-with-partial-findings"),
]


@pytest.mark.parametrize("mode", _INCOMPLETE_MODES)
def test_validation_does_not_confirm_a_fix_from_partial_findings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    """A fixed issue missing from an incomplete run stays unverified.

    Args:
        monkeypatch: Pytest patch fixture.
        tmp_path: Pytest temporary directory.
        mode: How the plugin's run is incomplete.
    """
    source = _source(tmp_path)
    _install(monkeypatch, mode=mode, source=source)

    result = validate_applied_fixes([_fixed_suggestion(source)])

    if result is None:
        pytest.fail("Expected a validation result")
    assert_that(result.verified).is_equal_to(0)
    assert_that(result.unverified_by_tool).is_equal_to({_TOOL: 1})
    assert_that(result.new_issues).is_equal_to(0)
    assert_that(result.details).is_equal_to(["[E2] main.py:5 — tool check unmeasured"])


@pytest.mark.parametrize("mode", _INCOMPLETE_MODES)
def test_rerun_keeps_original_counts_for_partial_findings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    """The re-run neither confirms the fix nor replaces the measured counts.

    Args:
        monkeypatch: Pytest patch fixture.
        tmp_path: Pytest temporary directory.
        mode: How the plugin's run is incomplete.
    """
    source = _source(tmp_path)
    _install(monkeypatch, mode=mode, source=source)
    original_issues: list[BaseIssue] = [
        MockIssue(file=str(source), line=1, code="E1"),
        MockIssue(file=str(source), line=5, code="E2"),
    ]
    original = ToolResult(
        name=_TOOL,
        success=False,
        issues=list(original_issues),
        issues_count=2,
    )
    by_tool = {_TOOL: (original, original_issues)}

    result = verify_fixes(
        applied_suggestions=[_fixed_suggestion(source)],
        by_tool=by_tool,
    )

    if result is None:
        pytest.fail("Expected a validation result")
    assert_that(result.verified).is_equal_to(0)
    assert_that(result.unverified).is_equal_to(1)
    assert_that(by_tool[_TOOL][0]).is_same_as(original)
    assert_that(by_tool[_TOOL][0].issues_count).is_equal_to(2)


def test_rerun_leaves_the_registry_singleton_refusal_state_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A refusal during the re-run is reported, not recorded on the singleton.

    Args:
        monkeypatch: Pytest patch fixture.
        tmp_path: Pytest temporary directory.
    """
    source = _source(tmp_path)
    singleton = _install(monkeypatch, mode="refused", source=source)
    issue = MockIssue(file=str(source), line=1, code="E1")
    original = ToolResult(name=_TOOL, success=False, issues=[issue], issues_count=1)

    results = rerun_tools({_TOOL: (original, [issue])})

    assert_that(singleton.command_refusal).is_none()
    assert_that(results).is_length(1)
    rerun = (results or [])[0]
    assert_that(rerun.execution_failed).is_true()
    assert_that(rerun.findings_complete).is_false()
    assert_that(rerun.output).starts_with(f"lintro refused to run {_TOOL}:")


def test_validation_leaves_the_registry_singleton_refusal_state_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Validating a fix through a refused command keeps the singleton clean.

    Args:
        monkeypatch: Pytest patch fixture.
        tmp_path: Pytest temporary directory.
    """
    source = _source(tmp_path)
    singleton = _install(monkeypatch, mode="refused", source=source)

    result = validate_applied_fixes([_fixed_suggestion(source)])

    assert_that(singleton.command_refusal).is_none()
    if result is None:
        pytest.fail("Expected a validation result")
    assert_that(result.unverified).is_equal_to(1)
