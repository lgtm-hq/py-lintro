"""Clippy tool definition.

Clippy is Rust's official linter with hundreds of lint rules for correctness,
style, complexity, and performance. It runs via `cargo clippy` and requires
a Cargo.toml file in the project.
"""

# mypy: ignore-errors
# Note: mypy errors are suppressed because lintro runs mypy from file's directory,
# breaking package resolution. When run properly (mypy lintro/...), this file passes.

from __future__ import annotations

import os
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.clippy.clippy_parser import parse_clippy_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_positive_int,
)
from lintro.tools.core.timeout_utils import (
    create_timeout_result,
    run_subprocess_with_timeout,
)

# Constants for Clippy configuration
CLIPPY_DEFAULT_TIMEOUT: int = 120
CLIPPY_DEFAULT_PRIORITY: int = 85
CLIPPY_FILE_PATTERNS: list[str] = ["*.rs", "Cargo.toml"]


def _find_cargo_root(paths: list[str]) -> Path | None:
    """Return the nearest directory containing Cargo.toml for given paths.

    Args:
        paths: List of file paths to search from.

    Returns:
        Path to Cargo.toml directory, or None if not found.
    """
    roots: list[Path] = []
    for raw_path in paths:
        current = Path(raw_path).resolve()
        # If it's a file, start from its parent
        if current.is_file():
            current = current.parent
        # Search upward for Cargo.toml
        for candidate in [current] + list(current.parents):
            manifest = candidate / "Cargo.toml"
            if manifest.exists():
                roots.append(candidate)
                break

    if not roots:
        return None

    # Prefer a single root; if multiple, use common path when valid
    unique_roots = set(roots)
    if len(unique_roots) == 1:
        return roots[0]

    try:
        common = Path(os.path.commonpath([str(r) for r in unique_roots]))
    except ValueError:
        return None

    manifest = common / "Cargo.toml"
    return common if manifest.exists() else None


def _build_clippy_command(fix: bool = False) -> list[str]:
    """Build the cargo clippy command.

    Args:
        fix: Whether to include --fix flag.

    Returns:
        List of command arguments.
    """
    cmd = [
        "cargo",
        "clippy",
        "--all-targets",
        "--all-features",
        "--message-format=json",
    ]
    if fix:
        cmd.extend(["--fix", "--allow-dirty", "--allow-staged"])
    return cmd


@register_tool
@dataclass
class ClippyPlugin(BaseToolPlugin):
    """Clippy Rust linter plugin.

    This plugin integrates Rust's Clippy linter with Lintro for checking
    Rust code for correctness, style, and performance issues.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="clippy",
            description=("Rust linter for correctness, style, and performance"),
            can_fix=True,
            tool_type=ToolType.LINTER,
            file_patterns=CLIPPY_FILE_PATTERNS,
            priority=CLIPPY_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["clippy.toml", ".clippy.toml"],
            version_command=["rustc", "--version"],
            min_version=get_min_version(ToolName.CLIPPY),
            default_options={
                "timeout": CLIPPY_DEFAULT_TIMEOUT,
            },
            default_timeout=CLIPPY_DEFAULT_TIMEOUT,
        )

    def _verify_tool_version(self) -> ToolResult | None:
        """Verify that Rust toolchain meets minimum version requirements.

        Clippy version is tied to Rust version, so we check rustc version instead.

        Returns:
            Optional[ToolResult]: None if version check passes, or a skip result
                if it fails.
        """
        from lintro.tools.core.version_requirements import check_tool_version

        # Check Rust version instead of clippy version
        version_info = check_tool_version("clippy", ["rustc"])

        if version_info.version_check_passed:
            return None  # Version check passed

        # Version check failed - return skip result with warning
        skip_message = (
            f"Skipping {self.definition.name}: {version_info.error_message}. "
            f"Minimum required: {version_info.min_version}. "
            f"{version_info.install_hint}"
        )

        return ToolResult(
            name=self.definition.name,
            success=True,  # Not an error, just skipping
            output=skip_message,
            issues_count=0,
        )

    def set_options(  # type: ignore[override]
        self,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Clippy-specific options.

        Args:
            timeout: Timeout in seconds (default: 120).
            **kwargs: Additional options.
        """
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(timeout=timeout)
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return Clippy documentation URL for the given lint name.

        Args:
            code: Clippy lint name (e.g., "needless_return").

        Returns:
            URL to the Clippy lint documentation.
        """
        if code:
            return f"https://rust-lang.github.io/rust-clippy/master/index.html#{code}"
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run `cargo clippy` and parse linting issues.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No Rust files found to check.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cargo_root = _find_cargo_root(ctx.files)
        if cargo_root is None:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No Cargo.toml found; skipping clippy.",
                issues_count=0,
            )

        cmd = _build_clippy_command(fix=False)

        try:
            success_cmd, output = run_subprocess_with_timeout(
                tool=self,
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=str(cargo_root),
                tool_name="clippy",
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=ctx.timeout,
                cmd=cmd,
                tool_name="clippy",
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=timeout_result.issues_count,
                issues=timeout_result.issues,
            )

        issues = parse_clippy_output(output=output)
        issues_count = len(issues)

        # Preserve output when command fails with no parsed issues for debugging
        # When issues exist, they'll be displayed instead
        should_show_output = not success_cmd and issues_count == 0

        return ToolResult(
            name=self.definition.name,
            success=bool(success_cmd),
            output=output if should_show_output else None,
            issues_count=issues_count,
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run `cargo clippy --fix` then re-check for remaining issues.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No Rust files found to fix.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cargo_root = _find_cargo_root(ctx.files)
        if cargo_root is None:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No Cargo.toml found; skipping clippy.",
                issues_count=0,
                initial_issues_count=0,
                fixed_issues_count=0,
                remaining_issues_count=0,
            )

        check_cmd = _build_clippy_command(fix=False)

        # First, count issues before fixing
        try:
            success_check, output_check = run_subprocess_with_timeout(
                tool=self,
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=str(cargo_root),
                tool_name="clippy",
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=ctx.timeout,
                cmd=check_cmd,
                tool_name="clippy",
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=timeout_result.issues_count,
                issues=timeout_result.issues,
                initial_issues_count=0,
                fixed_issues_count=0,
                remaining_issues_count=1,
            )

        initial_issues = parse_clippy_output(output=output_check)
        initial_count = len(initial_issues)

        # Run fix
        fix_cmd = _build_clippy_command(fix=True)
        try:
            success_fix, output_fix = run_subprocess_with_timeout(
                tool=self,
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=str(cargo_root),
                tool_name="clippy",
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=ctx.timeout,
                cmd=fix_cmd,
                tool_name="clippy",
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=timeout_result.issues_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=1,
            )

        # Re-check after fix to count remaining issues
        try:
            success_after, output_after = run_subprocess_with_timeout(
                tool=self,
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=str(cargo_root),
                tool_name="clippy",
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=ctx.timeout,
                cmd=check_cmd,
                tool_name="clippy",
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=timeout_result.issues_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=1,
            )

        remaining_issues = parse_clippy_output(output=output_after)
        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)

        # Preserve output when command fails but no issues were parsed
        # This allows users to see error messages like compilation failures
        should_show_output = not success_after and remaining_count == 0

        return ToolResult(
            name=self.definition.name,
            success=remaining_count == 0,
            output=output_after if should_show_output else None,
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
        )
