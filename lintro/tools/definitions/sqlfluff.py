"""SQLFluff tool definition.

SQLFluff is a SQL linter and formatter with support for many SQL dialects.
It parses SQL into an AST and performs linting rules on top of it.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.sqlfluff.sqlfluff_parser import parse_sqlfluff_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.file_processor import FileProcessingResult
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_list,
    validate_str,
)

# Constants for SQLFluff configuration
SQLFLUFF_DEFAULT_TIMEOUT: int = 60
SQLFLUFF_DEFAULT_PRIORITY: int = 50
SQLFLUFF_FILE_PATTERNS: list[str] = ["*.sql"]
SQLFLUFF_DEFAULT_FORMAT: str = "json"


@register_tool
@dataclass
class SqlfluffPlugin(BaseToolPlugin):
    """SQLFluff SQL linter and formatter plugin.

    This plugin integrates SQLFluff with Lintro for linting and formatting
    SQL files.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="sqlfluff",
            description="SQL linter and formatter with dialect support",
            can_fix=True,
            tool_type=ToolType.LINTER | ToolType.FORMATTER,
            file_patterns=SQLFLUFF_FILE_PATTERNS,
            priority=SQLFLUFF_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".sqlfluff", "pyproject.toml"],
            version_command=["sqlfluff", "--version"],
            min_version=get_min_version(ToolName.SQLFLUFF),
            default_options={
                "timeout": SQLFLUFF_DEFAULT_TIMEOUT,
                "dialect": None,
                "exclude_rules": None,
                "rules": None,
                "templater": None,
            },
            default_timeout=SQLFLUFF_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        dialect: str | None = None,
        exclude_rules: list[str] | None = None,
        rules: list[str] | None = None,
        templater: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set SQLFluff-specific options.

        Args:
            dialect: SQL dialect (ansi, bigquery, postgres, mysql, snowflake,
                sqlite, etc.).
            exclude_rules: List of rules to exclude.
            rules: List of rules to include.
            templater: Templater to use (raw, jinja, python, placeholder).
            **kwargs: Other tool options.
        """
        validate_str(dialect, "dialect")
        validate_list(exclude_rules, "exclude_rules")
        validate_list(rules, "rules")
        validate_str(templater, "templater")

        options = filter_none_options(
            dialect=dialect,
            exclude_rules=exclude_rules,
            rules=rules,
            templater=templater,
        )
        super().set_options(**options, **kwargs)

    def _build_lint_command(self, files: list[str]) -> list[str]:
        """Build the sqlfluff lint command.

        Args:
            files: List of files to lint.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["sqlfluff", "lint", "--format", SQLFLUFF_DEFAULT_FORMAT]

        # Add dialect option
        dialect_opt = self.options.get("dialect")
        if dialect_opt is not None:
            cmd.extend(["--dialect", str(dialect_opt)])

        # Add exclude rules (comma-separated per SQLFluff CLI docs)
        exclude_rules_opt = self.options.get("exclude_rules")
        if isinstance(exclude_rules_opt, list) and exclude_rules_opt:
            cmd.extend(["--exclude-rules", ",".join(map(str, exclude_rules_opt))])

        # Add rules (comma-separated per SQLFluff CLI docs)
        rules_opt = self.options.get("rules")
        if isinstance(rules_opt, list) and rules_opt:
            cmd.extend(["--rules", ",".join(map(str, rules_opt))])

        # Add templater
        templater_opt = self.options.get("templater")
        if templater_opt is not None:
            cmd.extend(["--templater", str(templater_opt)])

        # Add end-of-options separator to handle filenames starting with '-'
        cmd.append("--")

        # Add files
        cmd.extend(files)

        return cmd

    def _build_fix_command(self, files: list[str]) -> list[str]:
        """Build the sqlfluff fix command.

        Args:
            files: List of files to fix.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["sqlfluff", "fix", "--force"]

        # Add dialect option
        dialect_opt = self.options.get("dialect")
        if dialect_opt is not None:
            cmd.extend(["--dialect", str(dialect_opt)])

        # Add exclude rules (comma-separated per SQLFluff CLI docs)
        exclude_rules_opt = self.options.get("exclude_rules")
        if isinstance(exclude_rules_opt, list) and exclude_rules_opt:
            cmd.extend(["--exclude-rules", ",".join(map(str, exclude_rules_opt))])

        # Add rules (comma-separated per SQLFluff CLI docs)
        rules_opt = self.options.get("rules")
        if isinstance(rules_opt, list) and rules_opt:
            cmd.extend(["--rules", ",".join(map(str, rules_opt))])

        # Add templater
        templater_opt = self.options.get("templater")
        if templater_opt is not None:
            cmd.extend(["--templater", str(templater_opt)])

        # Add end-of-options separator to handle filenames starting with '-'
        cmd.append("--")

        # Add files
        cmd.extend(files)

        return cmd

    def _process_single_file_check(
        self,
        file_path: str,
        timeout: int,
    ) -> FileProcessingResult:
        """Process a single SQL file with sqlfluff lint.

        Args:
            file_path: Path to the SQL file to process.
            timeout: Timeout in seconds for the sqlfluff command.

        Returns:
            FileProcessingResult with check results for this file.
        """
        cmd = self._build_lint_command(files=[str(file_path)])
        try:
            success, output = self._run_subprocess(cmd=cmd, timeout=timeout)
            issues = parse_sqlfluff_output(output=output)
            # success is False if issues exist or tool failed
            final_success = success and len(issues) == 0
            return FileProcessingResult(
                success=final_success,
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

    def _process_single_file_fix(
        self,
        file_path: str,
        timeout: int,
    ) -> FileProcessingResult:
        """Process a single SQL file with sqlfluff fix.

        Args:
            file_path: Path to the SQL file to fix.
            timeout: Timeout in seconds for the sqlfluff command.

        Returns:
            FileProcessingResult with fix results for this file.
        """
        cmd = self._build_fix_command(files=[str(file_path)])
        try:
            success, output = self._run_subprocess(cmd=cmd, timeout=timeout)
            return FileProcessingResult(
                success=success,
                output=output,
                issues=[],
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
        """Return SQLFluff documentation URL for the given rule code.

        Args:
            code: SQLFluff rule code (e.g., "LT01").

        Returns:
            URL to the SQLFluff rule documentation.
        """
        if code:
            return f"https://docs.sqlfluff.com/en/stable/rules.html#{code}"
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with SQLFluff.

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

        # Process files with progress bar support
        def processor(file_path: str) -> FileProcessingResult:
            return self._process_single_file_check(file_path, ctx.timeout)

        result = self._process_files_with_progress(
            files=ctx.files,
            processor=processor,
            timeout=ctx.timeout,
            label="Processing files",
        )

        return ToolResult(
            name=self.definition.name,
            success=result.all_success,
            output=result.build_output(timeout=ctx.timeout),
            issues_count=result.total_issues,
            issues=result.all_issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix issues in files with SQLFluff.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Process files with progress bar support
        def processor(file_path: str) -> FileProcessingResult:
            return self._process_single_file_fix(file_path, ctx.timeout)

        result = self._process_files_with_progress(
            files=ctx.files,
            processor=processor,
            timeout=ctx.timeout,
            label="Fixing files",
        )

        return ToolResult(
            name=self.definition.name,
            success=result.all_success,
            output=result.build_output(timeout=ctx.timeout),
            issues_count=0,
            issues=[],
        )
