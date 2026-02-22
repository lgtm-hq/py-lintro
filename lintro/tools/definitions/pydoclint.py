"""Pydoclint tool definition.

Pydoclint is a Python docstring linter that validates docstrings match
function signatures. It checks for missing, extra, or incorrectly documented
parameters, return values, and raised exceptions.

Configuration is read directly from [tool.pydoclint] in pyproject.toml.
See docs/tool-analysis/pydoclint-analysis.md for recommended settings.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.pydoclint.pydoclint_parser import parse_pydoclint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.file_processor import FileProcessingResult
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool

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

    def _process_single_file(
        self,
        file_path: str,
        timeout: int,
    ) -> FileProcessingResult:
        """Process a single Python file with pydoclint.

        Args:
            file_path: Path to the Python file to process.
            timeout: Timeout in seconds for the pydoclint command.

        Returns:
            FileProcessingResult with processing outcome.
        """
        cmd = self._build_command() + [str(file_path)]
        try:
            success, output = self._run_subprocess(cmd=cmd, timeout=timeout)
            issues = parse_pydoclint_output(output=output)
            return FileProcessingResult(
                success=success and len(issues) == 0,
                output=output,
                issues=issues,
            )
        except subprocess.TimeoutExpired:
            return FileProcessingResult(
                success=False,
                output="",
                issues=[],
                skipped=True,
            )
        except (OSError, ValueError, RuntimeError) as e:
            return FileProcessingResult(
                success=False,
                output="",
                issues=[],
                error=str(e),
            )

    def doc_url(self, code: str) -> str | None:
        """Return pydoclint documentation URL.

        Pydoclint uses a single configuration page for all rules.

        Args:
            code: Pydoclint code (e.g., "DOC301").

        Returns:
            URL to the pydoclint documentation.
        """
        return "https://jsh9.github.io/pydoclint/how_to_config.html"

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with pydoclint.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths=paths, options=options)
        if ctx.should_skip:
            # early_result is guaranteed to be ToolResult when should_skip=True
            return ctx.early_result  # type: ignore[return-value]

        result = self._process_files_with_progress(
            files=ctx.files,
            processor=lambda f: self._process_single_file(f, ctx.timeout),
            timeout=ctx.timeout,
        )

        return ToolResult(
            name=self.definition.name,
            success=result.all_success and result.total_issues == 0,
            output=result.build_output(timeout=ctx.timeout),
            issues_count=result.total_issues,
            issues=result.all_issues,
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
