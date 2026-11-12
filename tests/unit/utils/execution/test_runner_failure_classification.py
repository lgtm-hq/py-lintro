"""A package runner failing before a tool runs is an execution failure (#2043).

An npx or bunx fallback can be rejected by npm itself (an npmrc
``workspace=<name>`` against ``--workspaces=false``), exit with no output, or
fail to find the executable. The tool then never reports, so the result must
not read as findings or as a pass. A local binary keeps its own verdict even
when its output mentions npm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
from lintro.utils.execution.result_shaping import (
    classify_runner_failure,
    executed_command,
    is_runner_command,
)

_NPM_CONFLICT = (
    "npm error Cannot use --no-workspaces and --workspace at the same time\n"
    "npm error A complete log of this run can be found in: /tmp/x.log\n"
)
_NPX = ["npx", "--yes", "--package", "prettier@3.9.4", "prettier"]
_LOCAL = ["/proj/node_modules/.bin/prettier"]


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
        cwd="/proj",
    )


@pytest.mark.parametrize(
    ("command", "output", "reason"),
    [
        (_NPX, _NPM_CONFLICT, "npm failed before the tool produced a report:"),
        (
            _NPX,
            "npm ERR! code ENOWORKSPACES\nnpm ERR! no workspaces\n",
            "npm failed before the tool produced a report:",
        ),
        (
            _NPX,
            "\x1b[31mnpm error\x1b[39m Cannot use --no-workspaces\n",
            "npm failed before the tool produced a report:",
        ),
        (_NPX, "", "npx exited with a failure and no output."),
        (_NPX, "  \n", "npx exited with a failure and no output."),
        (
            _NPX,
            "sh: prettier: command not found\n",
            "npx could not find the tool's executable:",
        ),
        (
            _NPX,
            "sh: 1: prettier: not found\n",
            "npx could not find the tool's executable:",
        ),
        (
            ["bunx", "prettier@3.9.4"],
            "",
            "bunx exited with a failure and no output.",
        ),
        (
            ["C:\\npm\\npx.cmd", "--yes", "--package", "prettier@3.9.4", "prettier"],
            _NPM_CONFLICT,
            "npm failed before the tool produced a report:",
        ),
    ],
    ids=[
        "npm-error",
        "legacy-npm-err",
        "ansi-coloured",
        "no-output",
        "blank-output",
        "command-not-found",
        "dash-not-found",
        "bunx-no-output",
        "windows-npx-cmd",
    ],
)
def test_runner_failure_without_a_report_is_an_execution_failure(
    command: list[str],
    output: str,
    reason: str,
) -> None:
    """A runner that failed with nothing from the tool is not a verdict.

    Args:
        command: Runner command that ran.
        output: Its output.
        reason: Expected start of the classified output.
    """
    result = classify_runner_failure(_failed(output), command=command)

    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.output).starts_with(reason)
    assert_that(result.output).does_not_contain("\x1b[")


@pytest.mark.parametrize(
    "output",
    [_NPM_CONFLICT, "", "sh: prettier: command not found\n"],
    ids=["npm-error-line", "no-output", "command-not-found"],
)
def test_local_binary_keeps_its_own_verdict(output: str) -> None:
    """A local prettier whose config prints ``npm error`` is not misattributed.

    Args:
        output: What the local binary printed.
    """
    original = _failed(output)

    assert_that(classify_runner_failure(original, command=_LOCAL)).is_equal_to(original)


def test_unknown_command_keeps_the_verdict() -> None:
    """Without knowing what ran, nothing is attributed to npm."""
    original = _failed(_NPM_CONFLICT)

    assert_that(classify_runner_failure(original, command=None)).is_equal_to(original)


@pytest.mark.parametrize(
    ("result", "case"),
    [
        (_failed("[warn] data.json\n"), "tool-report"),
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
def test_other_runner_results_are_left_unchanged(result: ToolResult, case: str) -> None:
    """Only a findings-free runner failure is reclassified.

    Args:
        result: Result that must pass through untouched.
        case: Scenario name.
    """
    del case

    assert_that(classify_runner_failure(result, command=_NPX)).is_equal_to(result)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (_NPX, True),
        (["/usr/local/bin/npx", "--yes"], True),
        (["bunx", "prettier@3.9.4"], True),
        (["C:\\npm\\NPX.CMD"], True),
        (["C:\\bun\\bunx.exe"], True),
        (_LOCAL, False),
        (["node", "npx"], False),
        ([], False),
        (None, False),
    ],
)
def test_runner_commands_are_recognised(
    command: list[str] | None,
    expected: bool,
) -> None:
    """Only ``npx``/``bunx`` as ``argv[0]`` count as runner commands.

    Args:
        command: Command prefix.
        expected: Whether it is a runner command.
    """
    assert_that(is_runner_command(command)).is_equal_to(expected)


@dataclass
class _Definition:
    name: str = "prettier"


@dataclass
class _Plugin:
    """Plugin stand-in whose resolver records the directory it was asked about."""

    command: list[str]
    asked: list[str | Path | None] = field(default_factory=list)
    definition: _Definition = field(default_factory=_Definition)

    def _get_executable_command(
        self,
        tool_name: str,
        cwd: str | Path | None = None,
    ) -> list[str]:
        del tool_name
        self.asked.append(cwd)
        return self.command


def test_executed_command_resolves_from_the_result_directory() -> None:
    """The plugin's resolver is asked about the directory the result ran in."""
    plugin = _Plugin(command=_NPX)

    command = executed_command(plugin, _failed(_NPM_CONFLICT))  # type: ignore[arg-type]

    assert_that(command).is_equal_to(_NPX)
    assert_that(plugin.asked).is_equal_to(["/proj"])


@pytest.mark.parametrize(
    "result",
    [
        ToolResult.from_outcome(
            ToolOutcome(execution=Checked(success=True)),
            name="prettier",
            cwd="/proj",
        ),
        ToolResult.from_outcome(
            ToolOutcome(execution=Checked(success=False)),
            name="prettier",
            output=_NPM_CONFLICT,
        ),
    ],
    ids=["passed", "no-cwd"],
)
def test_executed_command_is_unknown_without_a_failure_or_directory(
    result: ToolResult,
) -> None:
    """Passing results and results without a ``cwd`` are not resolved.

    Args:
        result: Result that must not be resolved.
    """
    plugin = _Plugin(command=_NPX)

    assert_that(executed_command(plugin, result)).is_none()  # type: ignore[arg-type]
    assert_that(plugin.asked).is_empty()
