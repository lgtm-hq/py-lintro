"""Typos tool definition.

typos is a source-code spell checker written in Rust. It finds and corrects
misspellings in code and documentation with a very low false-positive rate,
understanding programming conventions (identifiers, escape sequences, etc.).
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.typos.typos_parser import parse_typos_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool

# Constants for typos configuration
TYPOS_DEFAULT_TIMEOUT: int = 30
TYPOS_DEFAULT_PRIORITY: int = 50
# typos inspects all text files; binary files are detected and skipped by the
# tool itself, so a catch-all pattern is appropriate here.
TYPOS_FILE_PATTERNS: list[str] = ["*"]
TYPOS_DEFAULT_FORMAT: str = "json"


@register_tool
@dataclass
class TyposPlugin(BaseToolPlugin):
    """typos spell-checker plugin for Lintro.

    Integrates typos with Lintro to detect (and optionally auto-correct)
    misspellings in source code and documentation.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="typos",
            description=(
                "Source-code spell checker that finds and corrects typos with a "
                "low false-positive rate"
            ),
            can_fix=True,
            tool_type=ToolType.LINTER,
            file_patterns=TYPOS_FILE_PATTERNS,
            priority=TYPOS_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["typos.toml", ".typos.toml", "_typos.toml"],
            version_command=["typos", "--version"],
            min_version=get_min_version(ToolName.TYPOS),
            default_options={
                "timeout": TYPOS_DEFAULT_TIMEOUT,
            },
            default_timeout=TYPOS_DEFAULT_TIMEOUT,
        )

    def set_options(self, **kwargs: Any) -> None:
        """Set typos-specific options.

        Args:
            **kwargs: Tool options (currently only shared options such as
                ``timeout`` are supported).
        """
        super().set_options(**kwargs)

    def _build_command(self) -> list[str]:
        """Build the base typos command.

        Returns:
            List of command arguments (without file paths).
        """
        return ["typos", "--format", TYPOS_DEFAULT_FORMAT]

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files for typos.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths=paths, options=options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd = self._build_command() + ctx.rel_files
        try:
            success, output = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"typos timed out after {ctx.timeout}s",
                issues_count=0,
                cwd=ctx.cwd,
            )

        issues = parse_typos_output(output=output)

        return ToolResult(
            name=self.definition.name,
            success=success and len(issues) == 0,
            output=output if issues else None,
            issues_count=len(issues),
            issues=issues,
            cwd=ctx.cwd,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Auto-correct typos with ``typos --write-changes``.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results, satisfying the invariant
            ``initial = fixed + remaining``.
        """
        ctx = self._prepare_execution(
            paths=paths,
            options=options,
            no_files_message="No files to fix.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Detect issues before fixing.
        check_cmd = self._build_command() + ctx.rel_files
        try:
            _, initial_output = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"typos timed out after {ctx.timeout}s",
                issues_count=0,
                cwd=ctx.cwd,
            )

        initial_issues = parse_typos_output(output=initial_output)
        initial_count = len(initial_issues)

        # Apply corrections in place.
        fix_cmd = ["typos", "--write-changes", *ctx.rel_files]
        try:
            self._run_subprocess(cmd=fix_cmd, timeout=ctx.timeout, cwd=ctx.cwd)
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"typos timed out after {ctx.timeout}s",
                issues_count=initial_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=initial_count,
                initial_issues=initial_issues or None,
                cwd=ctx.cwd,
            )

        # Re-check for anything typos could not auto-correct.
        try:
            _, remaining_output = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"typos timed out after {ctx.timeout}s",
                issues_count=initial_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=initial_count,
                initial_issues=initial_issues or None,
                cwd=ctx.cwd,
            )

        remaining_issues = parse_typos_output(output=remaining_output)
        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)

        if fixed_count and remaining_count:
            summary = (
                f"Fixed {fixed_count} typo(s); {remaining_count} could not be "
                "auto-corrected."
            )
        elif fixed_count:
            summary = f"Fixed {fixed_count} typo(s)."
        elif remaining_count:
            summary = f"Found {remaining_count} typo(s) that could not be fixed."
        else:
            summary = "No typos found."

        all_issues = list(initial_issues) + list(remaining_issues)

        return ToolResult(
            name=self.definition.name,
            success=remaining_count == 0,
            output=summary,
            issues_count=remaining_count,
            issues=all_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=initial_issues or None,
            cwd=ctx.cwd,
        )
