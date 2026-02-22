"""Shellcheck tool definition.

ShellCheck is a static analysis tool for shell scripts. It identifies bugs,
syntax issues, and suggests improvements for bash/sh/dash/ksh scripts.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.shellcheck.shellcheck_parser import parse_shellcheck_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.file_processor import FileProcessingResult
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_list,
    validate_str,
)

# Constants for Shellcheck configuration
SHELLCHECK_DEFAULT_TIMEOUT: int = 30
SHELLCHECK_DEFAULT_PRIORITY: int = 50
SHELLCHECK_FILE_PATTERNS: list[str] = ["*.sh", "*.bash", "*.ksh"]
SHELLCHECK_DEFAULT_FORMAT: str = "json1"
SHELLCHECK_DEFAULT_SEVERITY: str = "style"

# Valid severity levels for shellcheck
SHELLCHECK_SEVERITY_LEVELS: tuple[str, ...] = ("error", "warning", "info", "style")

# Valid shell dialects for shellcheck (official: bash, sh, dash, ksh)
SHELLCHECK_SHELL_DIALECTS: tuple[str, ...] = ("bash", "sh", "dash", "ksh")


def normalize_shellcheck_severity(value: str) -> str:
    """Normalize shellcheck severity level.

    Args:
        value: Severity level string to normalize.

    Returns:
        Normalized severity level string (lowercase).

    Raises:
        ValueError: If the severity level is not valid.
    """
    normalized = value.lower()
    if normalized not in SHELLCHECK_SEVERITY_LEVELS:
        valid = ", ".join(SHELLCHECK_SEVERITY_LEVELS)
        raise ValueError(f"Invalid severity level: {value!r}. Valid levels: {valid}")
    return normalized


def normalize_shellcheck_shell(value: str) -> str:
    """Normalize shellcheck shell dialect.

    Args:
        value: Shell dialect string to normalize.

    Returns:
        Normalized shell dialect string (lowercase).

    Raises:
        ValueError: If the shell dialect is not valid.
    """
    normalized = value.lower()
    if normalized not in SHELLCHECK_SHELL_DIALECTS:
        valid = ", ".join(SHELLCHECK_SHELL_DIALECTS)
        raise ValueError(f"Invalid shell dialect: {value!r}. Valid dialects: {valid}")
    return normalized


@register_tool
@dataclass
class ShellcheckPlugin(BaseToolPlugin):
    """ShellCheck shell script linter plugin.

    This plugin integrates ShellCheck with Lintro for checking shell scripts
    against best practices and identifying potential bugs.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="shellcheck",
            description=(
                "Static analysis tool for shell scripts that identifies bugs and "
                "suggests improvements"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=SHELLCHECK_FILE_PATTERNS,
            priority=SHELLCHECK_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".shellcheckrc"],
            version_command=["shellcheck", "--version"],
            min_version=get_min_version(ToolName.SHELLCHECK),
            default_options={
                "timeout": SHELLCHECK_DEFAULT_TIMEOUT,
                "severity": SHELLCHECK_DEFAULT_SEVERITY,
                "exclude": None,
                "shell": None,
            },
            default_timeout=SHELLCHECK_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        severity: str | None = None,
        exclude: list[str] | None = None,
        shell: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Shellcheck-specific options.

        Args:
            severity: Minimum severity to report (error, warning, info, style).
            exclude: List of codes to exclude (e.g., ["SC2086", "SC2046"]).
            shell: Force shell dialect (bash, sh, dash, ksh).
            **kwargs: Other tool options.
        """
        if severity is not None:
            severity = normalize_shellcheck_severity(severity)

        if shell is not None:
            shell = normalize_shellcheck_shell(shell)

        validate_list(exclude, "exclude")
        validate_str(severity, "severity")
        validate_str(shell, "shell")

        options = filter_none_options(
            severity=severity,
            exclude=exclude,
            shell=shell,  # nosec B604 - shell is dialect, not subprocess shell=True
        )
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return ShellCheck wiki URL for the given code.

        Args:
            code: ShellCheck code (e.g., "SC2086").

        Returns:
            URL to the ShellCheck wiki page.
        """
        if code:
            return f"https://www.shellcheck.net/wiki/{code}"
        return None

    def _build_command(self) -> list[str]:
        """Build the shellcheck command.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["shellcheck"]

        # Always use json1 format for reliable parsing
        cmd.extend(["--format", SHELLCHECK_DEFAULT_FORMAT])

        # Add severity option
        severity = str(self.options.get("severity") or SHELLCHECK_DEFAULT_SEVERITY)
        cmd.extend(["--severity", severity])

        # Add exclude codes
        exclude_opt = self.options.get("exclude")
        if exclude_opt is not None and isinstance(exclude_opt, list):
            for code in exclude_opt:
                cmd.extend(["--exclude", str(code)])

        # Add shell dialect
        shell_opt = self.options.get("shell")
        if shell_opt is not None:
            cmd.extend(["--shell", str(shell_opt)])

        return cmd

    def _process_single_file(
        self,
        file_path: str,
        timeout: int,
    ) -> FileProcessingResult:
        """Process a single shell script with shellcheck.

        Args:
            file_path: Path to the shell script to process.
            timeout: Timeout in seconds for the shellcheck command.

        Returns:
            FileProcessingResult with processing outcome.
        """
        cmd = self._build_command() + [str(file_path)]
        try:
            success, output = self._run_subprocess(cmd=cmd, timeout=timeout)
            issues = parse_shellcheck_output(output=output)
            return FileProcessingResult(
                success=success,
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

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Shellcheck.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths=paths, options=options)
        if ctx.should_skip:
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
        """Shellcheck cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Shellcheck does not support fixing issues.
        """
        raise NotImplementedError(
            "Shellcheck cannot automatically fix issues. Run 'lintro check' to see "
            "issues.",
        )
