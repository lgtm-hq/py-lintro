"""Actionlint tool definition.

Actionlint is a static checker for GitHub Actions workflow files.
It validates workflow syntax, checks for common issues, and helps
maintain best practices in CI/CD workflows.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

import click

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.actionlint.actionlint_parser import parse_actionlint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool

# Constants for Actionlint configuration
ACTIONLINT_DEFAULT_TIMEOUT: int = 30
ACTIONLINT_DEFAULT_PRIORITY: int = 40
ACTIONLINT_FILE_PATTERNS: list[str] = ["*.yml", "*.yaml"]


@register_tool
@dataclass
class ActionlintPlugin(BaseToolPlugin):
    """GitHub Actions workflow linter plugin.

    This plugin integrates actionlint with Lintro for checking GitHub Actions
    workflow files against common issues and best practices.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="actionlint",
            description="Static checker for GitHub Actions workflows",
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.INFRASTRUCTURE,
            file_patterns=ACTIONLINT_FILE_PATTERNS,
            priority=ACTIONLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[],
            version_command=["actionlint", "--version"],
            min_version=get_min_version(ToolName.ACTIONLINT),
            default_options={
                "timeout": ACTIONLINT_DEFAULT_TIMEOUT,
            },
            default_timeout=ACTIONLINT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        **kwargs: Any,
    ) -> None:
        """Set Actionlint-specific options.

        Args:
            **kwargs: Other tool options.
        """
        super().set_options(**kwargs)

    def _build_command(self) -> list[str]:
        """Build the base actionlint command.

        We intentionally avoid flags here for maximum portability across
        platforms and actionlint versions. The tool's default text output
        follows the conventional ``file:line:col: message [CODE]`` format,
        which our parser handles directly without requiring a custom format
        switch.

        Returns:
            The base command list for invoking actionlint.
        """
        return ["actionlint"]

    def _process_single_file(
        self,
        file_path: str,
        timeout: int,
        results: dict[str, Any],
    ) -> None:
        """Process a single file with actionlint.

        Args:
            file_path: Path to the file to process.
            timeout: Timeout in seconds.
            results: Mutable dict to accumulate results.
        """
        cmd = self._build_command() + [file_path]
        try:
            success, output = self._run_subprocess(cmd=cmd, timeout=timeout)
            issues = parse_actionlint_output(output)

            if not success:
                results["all_success"] = False
            if output and (issues or not success):
                results["all_outputs"].append(output)
            if issues:
                results["all_issues"].extend(issues)
        except subprocess.TimeoutExpired:
            results["skipped_files"].append(file_path)
            results["all_success"] = False
            results["execution_failures"] += 1
        except (OSError, ValueError, RuntimeError) as e:  # pragma: no cover
            results["all_success"] = False
            results["all_outputs"].append(f"Error checking {file_path}: {e}")
            results["execution_failures"] += 1

    def doc_url(self, code: str) -> str | None:
        """Return actionlint documentation URL.

        Actionlint uses a single documentation page for all checks.

        Args:
            code: Actionlint check identifier (unused, single doc page).

        Returns:
            URL to the actionlint checks documentation.
        """
        return "https://github.com/rhysd/actionlint/blob/main/docs/checks.md"

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check GitHub Actions workflow files with actionlint.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Restrict to GitHub Actions workflow location
        workflow_files: list[str] = [
            f for f in ctx.files if "/.github/workflows/" in f.replace("\\", "/")
        ]

        if not workflow_files:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No GitHub workflow files found to check.",
                issues_count=0,
            )

        # Accumulate results across all files
        results: dict[str, Any] = {
            "all_outputs": [],
            "all_issues": [],
            "all_success": True,
            "skipped_files": [],
            "execution_failures": 0,
        }

        # Show progress bar only when processing multiple files
        if len(workflow_files) >= 2:
            with click.progressbar(
                workflow_files,
                label="Processing files",
                bar_template="%(label)s  %(info)s",
            ) as bar:
                for file_path in bar:
                    self._process_single_file(file_path, ctx.timeout, results)
        else:
            for file_path in workflow_files:
                self._process_single_file(file_path, ctx.timeout, results)

        # Build combined output
        combined_output = (
            "\n".join(results["all_outputs"]) if results["all_outputs"] else None
        )
        if results["skipped_files"]:
            timeout_msg = (
                f"Skipped {len(results['skipped_files'])} file(s) due to timeout "
                f"({ctx.timeout}s limit exceeded):"
            )
            for file in results["skipped_files"]:
                timeout_msg += f"\n  - {file}"
            combined_output = (
                f"{combined_output}\n\n{timeout_msg}"
                if combined_output
                else timeout_msg
            )

        non_timeout_failures = results["execution_failures"] - len(
            results["skipped_files"],
        )
        if non_timeout_failures > 0:
            failure_msg = (
                f"Failed to process {non_timeout_failures} file(s) "
                "due to execution errors"
            )
            combined_output = (
                f"{combined_output}\n\n{failure_msg}"
                if combined_output
                else failure_msg
            )

        return ToolResult(
            name=self.definition.name,
            success=results["all_success"],
            output=combined_output,
            issues_count=len(results["all_issues"]),
            issues=results["all_issues"],
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Actionlint cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Actionlint does not support fixing issues.
        """
        raise NotImplementedError(
            "Actionlint cannot automatically fix issues. Run 'lintro check' to see "
            "issues.",
        )
