"""An in-memory plugin whose command lintro refuses to spawn (#3486, #3497).

Shared by the tests of every consumer that must not read a refused tool as
clean: the AI review lint digest and the advisory runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition

#: A Node shim and a repository path holding ``&``, legal on Windows; lintro
#: refuses it because ``cmd.exe`` would interpret the ``&``.
REFUSED_COMMAND: tuple[str, ...] = (
    "C:/repo/node_modules/.bin/tsc.cmd",
    "a&whoami&b",
)

#: The finding a refusing plugin reports from the invocation that did run.
FINDING_MESSAGE: str = "real finding from the run that completed"


@dataclass
class RefusingPlugin(BaseToolPlugin):
    """Plugin whose second invocation is refused, as a multi-run tool's can be.

    Attributes:
        tool_name: Name the plugin reports.
        with_finding: Whether the first invocation reported a finding.
        command: The command the refused invocation runs.
    """

    tool_name: str = "tsc"
    with_finding: bool = False
    command: tuple[str, ...] = REFUSED_COMMAND

    @property
    def definition(self) -> ToolDefinition:
        """Return metadata for the in-memory plugin.

        Returns:
            A check-only tool definition.
        """
        return ToolDefinition(name=self.tool_name, description="Refusing plugin")

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run the refused command and report it as an ordinary failed verdict.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            A failed verdict carrying the error and any finding, as several
            real plugins report a launch ``ValueError``.
        """
        issues = (
            [BaseIssue(file="src/app.ts", line=3, column=1, message=FINDING_MESSAGE)]
            if self.with_finding
            else []
        )
        try:
            self._run_subprocess(list(self.command))
        except ValueError as exc:
            return ToolResult.from_outcome(
                ToolOutcome(execution=Checked(success=False), issues_count=len(issues)),
                name=self.tool_name,
                issues=issues,
                output=f"Execution failed: {exc}",
            )
        return ToolResult(name=self.tool_name, success=True)

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run the same command in fix mode.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            The check result.
        """
        return self.check(paths, options)


def copy_configured(**kwargs: object) -> BaseToolPlugin:
    """Stand in for configuration: a fresh per-run copy, as the real one makes.

    Args:
        **kwargs: ``configure_tool_for_execution`` keyword arguments.

    Returns:
        A per-run copy of the template tool.
    """
    return cast(BaseToolPlugin, kwargs["tool"]).copy_for_execution()
