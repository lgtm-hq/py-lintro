"""Rustfmt tool definition.

Rustfmt is Rust's official code formatter. It enforces a consistent style
by parsing Rust code and re-printing it with its own rules. It runs via
`cargo fmt` and requires a Cargo.toml file in the project.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.rustfmt.rustfmt_parser import parse_rustfmt_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.batch_runner import (
    BatchCheckPolicy,
    BatchOutput,
    batch_fix_timeout_result,
    run_batch_check,
)
from lintro.tools.core.cargo import find_cargo_root
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_positive_int,
)
from lintro.tools.core.timeout_utils import (
    run_subprocess_with_timeout,
)

# Constants for Rustfmt configuration
RUSTFMT_DEFAULT_TIMEOUT: int = 60
RUSTFMT_DEFAULT_PRIORITY: int = 80  # Formatter, runs after linters
RUSTFMT_FILE_PATTERNS: list[str] = ["*.rs"]


def _build_rustfmt_check_command() -> list[str]:
    """Build the cargo fmt check command.

    Returns:
        List of command arguments.
    """
    return ["cargo", "fmt", "--all", "--", "--check"]


def _build_rustfmt_fix_command() -> list[str]:
    """Build the cargo fmt fix command.

    Returns:
        List of command arguments.
    """
    return ["cargo", "fmt", "--all"]


@register_tool
@dataclass
class RustfmtPlugin(BaseToolPlugin):
    """Rustfmt Rust formatter plugin.

    This plugin integrates Rust's rustfmt formatter with Lintro for formatting
    Rust code consistently.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="rustfmt",
            description="Rust's official code formatter",
            can_fix=True,
            tool_type=ToolType.FORMATTER,
            file_patterns=RUSTFMT_FILE_PATTERNS,
            priority=RUSTFMT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["rustfmt.toml", ".rustfmt.toml"],
            version_command=["rustfmt", "--version"],
            min_version=get_min_version(ToolName.RUSTFMT),
            default_options={
                "timeout": RUSTFMT_DEFAULT_TIMEOUT,
            },
            default_timeout=RUSTFMT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Rustfmt-specific options.

        Args:
            timeout: Timeout in seconds (default: 60).
            **kwargs: Additional options.
        """
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(timeout=timeout)
        super().set_options(**options, **kwargs)

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run `cargo fmt -- --check` and parse formatting issues.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self.prepare(
            paths,
            options,
            no_files_message="No Rust files found to check.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        cargo_root = find_cargo_root(ctx.files, tool_label="rustfmt")
        if cargo_root is None:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No Cargo.toml found; skipping rustfmt.",
                issues_count=0,
            )

        cmd = _build_rustfmt_check_command()

        # `cargo fmt --check` exits non-zero both for diffs and for real
        # failures, so keep the raw output whenever anything went wrong.
        return run_batch_check(
            ctx,
            plugin=self,
            cmd=cmd,
            parse=lambda output: parse_rustfmt_output(output=output),
            policy=BatchCheckPolicy(
                output=BatchOutput.ON_ISSUES_OR_EXIT_FAILURE,
                tool_name="rustfmt",
            ),
            cwd=str(cargo_root),
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run `cargo fmt --all` then re-check for remaining issues.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self.prepare(
            paths,
            options,
            no_files_message="No Rust files found to fix.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        cargo_root = find_cargo_root(ctx.files, tool_label="rustfmt")
        if cargo_root is None:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No Cargo.toml found; skipping rustfmt.",
                issues_count=0,
                initial_issues_count=0,
                fixed_issues_count=0,
                remaining_issues_count=0,
            )

        check_cmd = _build_rustfmt_check_command()

        # First, count issues before fixing
        try:
            _, output_check = run_subprocess_with_timeout(
                tool=self,
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=str(cargo_root),
                tool_name="rustfmt",
            )
        except subprocess.TimeoutExpired:
            # Timeout on initial check - can't determine issue counts
            return batch_fix_timeout_result(
                plugin=self,
                timeout=ctx.timeout,
                initial_issues=[],
                cmd=check_cmd,
                tool_name="rustfmt",
            )

        initial_issues = parse_rustfmt_output(output=output_check)
        initial_count = len(initial_issues)

        # Run fix
        fix_cmd = _build_rustfmt_fix_command()
        try:
            fix_success, fix_output = run_subprocess_with_timeout(
                tool=self,
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=str(cargo_root),
                tool_name="rustfmt",
            )
        except subprocess.TimeoutExpired:
            return batch_fix_timeout_result(
                plugin=self,
                timeout=ctx.timeout,
                initial_issues=initial_issues,
                cmd=fix_cmd,
                tool_name="rustfmt",
            )

        # If fix command failed, return early with the fix output
        if not fix_success:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=fix_output,
                issues_count=initial_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=initial_count,
                initial_issues=initial_issues if initial_issues else None,
            )

        # Re-check after fix to count remaining issues
        try:
            verify_success, output_after = run_subprocess_with_timeout(
                tool=self,
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=str(cargo_root),
                tool_name="rustfmt",
            )
        except subprocess.TimeoutExpired:
            return batch_fix_timeout_result(
                plugin=self,
                timeout=ctx.timeout,
                initial_issues=initial_issues,
                cmd=check_cmd,
                tool_name="rustfmt",
            )

        remaining_issues = parse_rustfmt_output(output=output_after)
        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)

        # Success requires both: verification passed AND no remaining issues
        overall_success = verify_success and remaining_count == 0

        return ToolResult(
            name=self.definition.name,
            success=overall_success,
            output=output_after if not overall_success else None,
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=initial_issues if initial_issues else None,
        )
