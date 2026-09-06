"""Pydoclint tool definition.

Pydoclint is a Python docstring linter that validates docstrings match
function signatures. It checks for missing, extra, or incorrectly documented
parameters, return values, and raised exceptions.

Configuration is read directly from [tool.pydoclint] in pyproject.toml.
See docs/tool-analysis/pydoclint-analysis.md for recommended settings.
"""

from __future__ import annotations

from dataclasses import dataclass

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.pydoclint.pydoclint_parser import parse_pydoclint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.check_runner import PerFileCheckPolicy, run_per_file_check

# Constants for Pydoclint configuration
PYDOCLINT_DEFAULT_TIMEOUT: int = 30
PYDOCLINT_DEFAULT_PRIORITY: int = 45
PYDOCLINT_FILE_PATTERNS: list[str] = ["*.py", "*.pyi"]


@register_tool
@dataclass
class PydoclintPlugin(BaseToolPlugin):
    """Pydoclint Python docstring linter plugin.

    This plugin integrates pydoclint with Lintro for validating Python
    docstrings match function signatures. Pydoclint reads its configuration
    directly from [tool.pydoclint] in pyproject.toml.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition."""
        return ToolDefinition(
            name="pydoclint",
            description=(
                "Python docstring linter that validates docstrings match "
                "function signatures"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.DOCUMENTATION,
            file_patterns=PYDOCLINT_FILE_PATTERNS,
            priority=PYDOCLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["pyproject.toml", ".pydoclint.toml"],
            version_command=["pydoclint", "--version"],
            default_options={
                "timeout": PYDOCLINT_DEFAULT_TIMEOUT,
                "quiet": True,
            },
            default_timeout=PYDOCLINT_DEFAULT_TIMEOUT,
        )

    def _build_command(self) -> list[str]:
        """Build the pydoclint command.

        pydoclint reads most options from [tool.pydoclint] in pyproject.toml.
        We only add --quiet for cleaner lintro output.
        """
        cmd: list[str] = ["pydoclint"]

        if self.options.get("quiet", True):
            cmd.append("--quiet")

        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return pydoclint documentation URL.

        Pydoclint uses a single configuration page for all rules.

        Args:
            code: Pydoclint code (e.g., "DOC301").

        Returns:
            URL to the pydoclint documentation, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.PYDOCLINT

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with pydoclint.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self.prepare(paths=paths, options=options)
        if isinstance(ctx, ToolResult):
            return ctx

        return run_per_file_check(
            ctx,
            plugin=self,
            command=lambda f: [*self._build_command(), str(f)],
            parse=lambda output: parse_pydoclint_output(output=output),
            policy=PerFileCheckPolicy(issues_imply_failure=True),
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Pydoclint cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Pydoclint does not support fixing issues.
        """
        raise NotImplementedError(
            "Pydoclint cannot automatically fix issues. Run 'lintro check' to see "
            "issues.",
        )
