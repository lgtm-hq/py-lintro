"""Shfmt tool definition.

Shfmt is a shell script formatter that supports POSIX, Bash, and mksh shells.
It formats shell scripts to ensure consistent style and can detect formatting
issues in diff mode.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.shfmt.shfmt_parser import parse_shfmt_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.file_processor import FileProcessingResult
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.fix_runner import PerFileFixPolicy, run_per_file_fix
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_int,
    validate_str,
)

# Constants for shfmt configuration
SHFMT_DEFAULT_TIMEOUT: int = 30
SHFMT_DEFAULT_PRIORITY: int = 50
SHFMT_FILE_PATTERNS: list[str] = ["*.sh", "*.bash", "*.ksh"]


@register_tool
@dataclass
class ShfmtPlugin(BaseToolPlugin):
    """Shfmt shell script formatter plugin.

    This plugin integrates shfmt with Lintro for formatting shell scripts.
    It supports POSIX, Bash, and mksh shells with various formatting options.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="shfmt",
            description=(
                "Shell script formatter supporting POSIX, Bash, and mksh shells"
            ),
            can_fix=True,
            tool_type=ToolType.FORMATTER,
            file_patterns=SHFMT_FILE_PATTERNS,
            priority=SHFMT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".editorconfig"],
            version_command=["shfmt", "--version"],
            min_version=get_min_version(ToolName.SHFMT),
            default_options={
                "timeout": SHFMT_DEFAULT_TIMEOUT,
                "indent": None,
                "binary_next_line": False,
                "switch_case_indent": False,
                "space_redirects": False,
                "language_dialect": None,
                "simplify": False,
            },
            default_timeout=SHFMT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        indent: int | None = None,
        binary_next_line: bool | None = None,
        switch_case_indent: bool | None = None,
        space_redirects: bool | None = None,
        language_dialect: str | None = None,
        simplify: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set shfmt-specific options.

        Args:
            indent: Indentation size. 0 for tabs, >0 for that many spaces.
            binary_next_line: Binary ops like && and | may start a line.
            switch_case_indent: Indent switch cases.
            space_redirects: Redirect operators followed by space.
            language_dialect: Shell language dialect (bash, posix, mksh, bats).
            simplify: Simplify code where possible.
            **kwargs: Other tool options.

        Raises:
            ValueError: If language_dialect is not a valid dialect.
        """
        validate_int(indent, "indent")
        validate_bool(binary_next_line, "binary_next_line")
        validate_bool(switch_case_indent, "switch_case_indent")
        validate_bool(space_redirects, "space_redirects")
        validate_str(language_dialect, "language_dialect")
        validate_bool(simplify, "simplify")

        # Validate language_dialect if provided
        if language_dialect is not None:
            valid_dialects = {"bash", "posix", "mksh", "bats"}
            if language_dialect.lower() not in valid_dialects:
                msg = (
                    f"Invalid language_dialect: {language_dialect!r}. "
                    f"Must be one of: {', '.join(sorted(valid_dialects))}"
                )
                raise ValueError(msg)
            language_dialect = language_dialect.lower()

        options = filter_none_options(
            indent=indent,
            binary_next_line=binary_next_line,
            switch_case_indent=switch_case_indent,
            space_redirects=space_redirects,
            language_dialect=language_dialect,
            simplify=simplify,
        )
        super().set_options(**options, **kwargs)

    def _build_common_args(self) -> list[str]:
        """Build common CLI arguments for shfmt.

        Returns:
            CLI arguments for shfmt.
        """
        args: list[str] = []

        # Indentation
        indent = self.options.get("indent")
        if indent is not None:
            args.extend(["-i", str(indent)])

        # Binary operations at start of line
        if self.options.get("binary_next_line"):
            args.append("-bn")

        # Switch case indentation
        if self.options.get("switch_case_indent"):
            args.append("-ci")

        # Space after redirect operators
        if self.options.get("space_redirects"):
            args.append("-sr")

        # Language dialect
        language_dialect = self.options.get("language_dialect")
        if language_dialect is not None:
            args.extend(["-ln", str(language_dialect)])

        # Simplify code
        if self.options.get("simplify"):
            args.append("-s")

        return args

    def _process_single_file(
        self,
        file_path: str,
        timeout: int,
    ) -> FileProcessingResult:
        """Process a single file in check mode.

        Args:
            file_path: Path to the shell script to check.
            timeout: Timeout in seconds for the shfmt command.

        Returns:
            FileProcessingResult with processing outcome.
        """
        try:
            success, output = self._run_subprocess(
                cmd=self._diff_command(file_path),
                timeout=timeout,
            )
            issues = parse_shfmt_output(output=output)
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
                timed_out=True,
            )
        except (OSError, ValueError, RuntimeError) as e:
            return FileProcessingResult(
                success=False,
                output="",
                issues=[],
                error=str(e),
            )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with shfmt.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self.prepare(paths, options)
        if isinstance(ctx, ToolResult):
            return ctx

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
            timed_out=result.timed_out,
            issues=result.all_issues,
        )

    def _diff_command(self, file_path: str) -> list[str]:
        """Build the diff-mode command used to detect formatting issues.

        Args:
            file_path: Path to the shell script to inspect.

        Returns:
            Command line for shfmt in diff mode.
        """
        return [
            *self._get_executable_command(tool_name="shfmt"),
            "-d",
            *self._build_common_args(),
            file_path,
        ]

    def _write_command(self, file_path: str) -> list[str]:
        """Build the write-mode command used to apply formatting.

        Args:
            file_path: Path to the shell script to format.

        Returns:
            Command line for shfmt in write mode.
        """
        return [
            *self._get_executable_command(tool_name="shfmt"),
            "-w",
            *self._build_common_args(),
            file_path,
        ]

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix formatting issues in files with shfmt.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        ctx = self.prepare(
            paths,
            options,
            no_files_message="No files to format.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        return run_per_file_fix(
            ctx,
            plugin=self,
            check_command=self._diff_command,
            fix_command=self._write_command,
            parse=lambda output: parse_shfmt_output(output=output),
            policy=PerFileFixPolicy(
                check_failure_message="shfmt check failed before fix",
                label="Formatting files",
            ),
        )
