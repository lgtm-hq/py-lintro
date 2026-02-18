"""Oxlint tool definition.

Oxlint is a fast JavaScript/TypeScript linter with 661+ built-in rules.
It provides fast linting with minimal configuration.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.oxlint.oxlint_issue import OxlintIssue
from lintro.parsers.oxlint.oxlint_parser import parse_oxlint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    normalize_str_or_list,
    validate_bool,
    validate_list,
    validate_positive_int,
    validate_str,
)

# Constants for Oxlint configuration
OXLINT_DEFAULT_TIMEOUT: int = 30
OXLINT_DEFAULT_PRIORITY: int = 50
OXLINT_FILE_PATTERNS: list[str] = [
    "*.js",
    "*.mjs",
    "*.cjs",
    "*.jsx",
    "*.ts",
    "*.mts",
    "*.cts",
    "*.tsx",
    "*.vue",
    "*.svelte",
    "*.astro",
]


@register_tool
@dataclass
class OxlintPlugin(BaseToolPlugin):
    """Oxlint JavaScript/TypeScript linter plugin.

    This plugin integrates Oxlint with Lintro for linting JavaScript,
    TypeScript, and related framework files.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="oxlint",
            description=("Fast JavaScript/TypeScript linter with 661+ built-in rules"),
            can_fix=True,
            tool_type=ToolType.LINTER,
            file_patterns=OXLINT_FILE_PATTERNS,
            priority=OXLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".oxlintrc.json"],
            version_command=["oxlint", "--version"],
            min_version=get_min_version(ToolName.OXLINT),
            default_options={
                "timeout": OXLINT_DEFAULT_TIMEOUT,
                "quiet": False,
            },
            default_timeout=OXLINT_DEFAULT_TIMEOUT,
        )

    def __post_init__(self) -> None:
        """Initialize the tool with default options."""
        super().__post_init__()
        self.options.setdefault("quiet", False)

    def set_options(  # type: ignore[override]
        self,
        exclude_patterns: list[str] | None = None,
        include_venv: bool = False,
        timeout: int | None = None,
        quiet: bool | None = None,
        verbose_fix_output: bool | None = None,
        config: str | None = None,
        tsconfig: str | None = None,
        allow: list[str] | str | None = None,
        deny: list[str] | str | None = None,
        warn: list[str] | str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Oxlint-specific options.

        Args:
            exclude_patterns: List of patterns to exclude.
            include_venv: Whether to include virtual environment directories.
            timeout: Timeout in seconds (default: 30).
            quiet: If True, suppress warnings and only report errors.
            verbose_fix_output: If True, include raw Oxlint output in fix().
            config: Path to Oxlint config file (--config).
            tsconfig: Path to tsconfig.json for TypeScript support (--tsconfig).
            allow: Rules to allow/turn off (--allow). Can be string or list.
            deny: Rules to deny/report as errors (--deny). Can be string or list.
            warn: Rules to warn on (--warn). Can be string or list.
            **kwargs: Additional options (ignored for compatibility).
        """
        validate_list(exclude_patterns, "exclude_patterns")
        validate_positive_int(timeout, "timeout")
        validate_bool(quiet, "quiet")
        validate_bool(verbose_fix_output, "verbose_fix_output")
        validate_str(config, "config")
        validate_str(tsconfig, "tsconfig")

        # Normalize rule lists (accept string or list)
        allow_list = normalize_str_or_list(allow, "allow")
        deny_list = normalize_str_or_list(deny, "deny")
        warn_list = normalize_str_or_list(warn, "warn")

        if exclude_patterns is not None:
            self.exclude_patterns = exclude_patterns.copy()
        self.include_venv = include_venv

        options = filter_none_options(
            timeout=timeout,
            quiet=quiet,
            verbose_fix_output=verbose_fix_output,
            config=config,
            tsconfig=tsconfig,
            allow=allow_list,
            deny=deny_list,
            warn=warn_list,
        )
        super().set_options(**options, **kwargs)

    def _create_timeout_result(
        self,
        timeout_val: int,
        initial_issues: list[OxlintIssue] | None = None,
        initial_count: int = 0,
    ) -> ToolResult:
        """Create a ToolResult for timeout scenarios.

        Args:
            timeout_val: The timeout value that was exceeded.
            initial_issues: Optional list of issues found before timeout.
            initial_count: Optional count of initial issues.

        Returns:
            ToolResult: ToolResult instance representing timeout failure.
        """
        timeout_msg = (
            f"Oxlint execution timed out ({timeout_val}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options oxlint:timeout=N"
        )
        timeout_issue = OxlintIssue(
            file="execution",
            line=1,
            column=1,
            code="TIMEOUT",
            message=timeout_msg,
            severity="error",
            fixable=False,
        )
        combined_issues = (initial_issues or []) + [timeout_issue]
        remaining_count = len(combined_issues)
        # Ensure consistency: if initial was 0, set it to remaining_count
        # so that initial = fixed + remaining holds (0 + remaining = remaining)
        effective_initial = initial_count if initial_count > 0 else remaining_count
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=timeout_msg,
            issues_count=remaining_count,
            issues=combined_issues,
            initial_issues_count=effective_initial,
            fixed_issues_count=0,
            remaining_issues_count=remaining_count,
        )

    def _build_oxlint_args(self, options: dict[str, object]) -> list[str]:
        """Build CLI arguments from options.

        Args:
            options: Options dict to build args from (use merged_options).

        Returns:
            List of CLI arguments to pass to oxlint.
        """
        args: list[str] = []

        # Config file override
        config = options.get("config")
        if config:
            args.extend(["--config", str(config)])

        # TypeScript config
        tsconfig = options.get("tsconfig")
        if tsconfig:
            args.extend(["--tsconfig", str(tsconfig)])

        # Rule severity options
        allow_rules = options.get("allow")
        if allow_rules and isinstance(allow_rules, list):
            for rule in allow_rules:
                args.extend(["--allow", rule])

        deny_rules = options.get("deny")
        if deny_rules and isinstance(deny_rules, list):
            for rule in deny_rules:
                args.extend(["--deny", rule])

        warn_rules = options.get("warn")
        if warn_rules and isinstance(warn_rules, list):
            for rule in warn_rules:
                args.extend(["--warn", rule])

        return args

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Oxlint without making changes.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No files to check.",
        )
        if ctx.should_skip:
            assert ctx.early_result is not None
            return ctx.early_result

        logger.debug(
            f"[OxlintPlugin] Discovered {len(ctx.files)} files matching patterns: "
            f"{self.definition.file_patterns}",
        )
        logger.debug(
            f"[OxlintPlugin] Exclude patterns applied: {self.exclude_patterns}",
        )
        if ctx.files:
            logger.debug(
                f"[OxlintPlugin] Files to check (first 10): {ctx.files[:10]}",
            )
        logger.debug(f"[OxlintPlugin] Working directory: {ctx.cwd}")

        # Build Oxlint command with JSON format
        cmd: list[str] = self._get_executable_command(tool_name="oxlint") + [
            "--format",
            "json",
        ]

        # Add quiet flag if enabled (suppress warnings, only report errors)
        if self.options.get("quiet", False):
            cmd.append("--quiet")

        # Add Lintro config injection args if available
        config_args = self._build_config_args()
        if config_args:
            cmd.extend(config_args)
            logger.debug("[OxlintPlugin] Using Lintro config injection")

        # Add Oxlint-specific CLI arguments from options
        oxlint_args = self._build_oxlint_args(merged_options)
        if oxlint_args:
            cmd.extend(oxlint_args)

        cmd.extend(ctx.rel_files)
        logger.debug(f"[OxlintPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")

        try:
            result = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout)

        output: str = result[1]
        issues: list[OxlintIssue] = parse_oxlint_output(output=output)
        issues_count: int = len(issues)
        success: bool = issues_count == 0

        # Standardize: suppress Oxlint's informational output when no issues
        final_output: str | None = output
        if success:
            final_output = None

        return ToolResult(
            name=self.definition.name,
            success=success,
            output=final_output,
            issues_count=issues_count,
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix auto-fixable issues in files with Oxlint.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Result object with counts and messages.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No files to fix.",
        )
        if ctx.should_skip:
            assert ctx.early_result is not None
            return ctx.early_result

        # Get Lintro config injection args if available
        config_args = self._build_config_args()

        # Add Oxlint-specific CLI arguments from options
        oxlint_args = self._build_oxlint_args(merged_options)

        # Build check command for counting issues
        check_cmd: list[str] = self._get_executable_command(tool_name="oxlint") + [
            "--format",
            "json",
        ]

        # Add quiet flag if enabled (suppress warnings, only report errors)
        if self.options.get("quiet", False):
            check_cmd.append("--quiet")

        if config_args:
            check_cmd.extend(config_args)
        if oxlint_args:
            check_cmd.extend(oxlint_args)

        check_cmd.extend(ctx.rel_files)
        logger.debug(
            f"[OxlintPlugin] Checking: {' '.join(check_cmd)} (cwd={ctx.cwd})",
        )

        # Check for initial issues
        try:
            check_result = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout)

        check_output: str = check_result[1]
        initial_issues: list[OxlintIssue] = parse_oxlint_output(output=check_output)
        initial_count: int = len(initial_issues)

        # Now fix the issues
        fix_cmd: list[str] = self._get_executable_command(tool_name="oxlint") + [
            "--fix",
        ]
        if config_args:
            fix_cmd.extend(config_args)
        if oxlint_args:
            fix_cmd.extend(oxlint_args)
        fix_cmd.extend(ctx.rel_files)
        logger.debug(f"[OxlintPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")

        try:
            fix_result = self._run_subprocess(
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(
                timeout_val=ctx.timeout,
                initial_issues=initial_issues,
                initial_count=initial_count,
            )
        fix_output: str = fix_result[1]

        # Check for remaining issues after fixing
        try:
            final_check_result = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(
                timeout_val=ctx.timeout,
                initial_issues=initial_issues,
                initial_count=initial_count,
            )

        final_check_output: str = final_check_result[1]
        remaining_issues: list[OxlintIssue] = parse_oxlint_output(
            output=final_check_output,
        )
        remaining_count: int = len(remaining_issues)

        # Calculate fixed issues
        fixed_count: int = max(0, initial_count - remaining_count)

        # Build output message
        output_lines: list[str] = []
        if fixed_count > 0:
            output_lines.append(f"Fixed {fixed_count} issue(s)")

        if remaining_count > 0:
            output_lines.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
            for issue in remaining_issues[:5]:
                output_lines.append(f"  {issue.file} - {issue.message}")
            if len(remaining_issues) > 5:
                output_lines.append(f"  ... and {len(remaining_issues) - 5} more")
        elif remaining_count == 0 and fixed_count > 0:
            output_lines.append("All issues were successfully auto-fixed")

        # Add verbose raw fix output only when explicitly requested
        if (
            merged_options.get("verbose_fix_output", False)
            and fix_output
            and fix_output.strip()
        ):
            output_lines.append(f"Fix output:\n{fix_output}")

        final_output: str | None = "\n".join(output_lines) if output_lines else None

        # Success means no remaining issues
        success: bool = remaining_count == 0

        return ToolResult(
            name=self.definition.name,
            success=success,
            output=final_output,
            issues_count=remaining_count,
            issues=remaining_issues or [],
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            cwd=ctx.cwd,
        )
