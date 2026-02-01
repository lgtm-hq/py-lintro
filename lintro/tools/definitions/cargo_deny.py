"""cargo-deny tool definition.

cargo-deny is a Rust tool that checks licenses, advisories, bans, and duplicate
dependencies in Cargo projects. It requires a Cargo.toml file and optionally
uses deny.toml for configuration.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.cargo_deny.cargo_deny_parser import parse_cargo_deny_output
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

# Constants for cargo-deny configuration
CARGO_DENY_DEFAULT_TIMEOUT: int = 60
CARGO_DENY_DEFAULT_PRIORITY: int = 90  # High priority for security tool
CARGO_DENY_FILE_PATTERNS: list[str] = ["Cargo.toml", "deny.toml"]


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
        for candidate in [current, *list(current.parents)]:
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


def _build_cargo_deny_command() -> list[str]:
    """Build the cargo deny check command.

    Returns:
        List of command arguments.
    """
    return [
        "cargo",
        "deny",
        "check",
        "--format",
        "json",
    ]


@register_tool
@dataclass
class CargoDenyPlugin(BaseToolPlugin):
    """cargo-deny security and compliance checker plugin.

    This plugin integrates cargo-deny with Lintro for checking Rust projects
    for license compliance, security advisories, banned dependencies, and
    duplicate dependencies.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="cargo_deny",
            description=(
                "Checks licenses, advisories, bans, and duplicate dependencies"
            ),
            can_fix=False,
            tool_type=ToolType.SECURITY | ToolType.INFRASTRUCTURE,
            file_patterns=CARGO_DENY_FILE_PATTERNS,
            priority=CARGO_DENY_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["deny.toml"],
            version_command=["cargo", "deny", "--version"],
            min_version="0.14.0",
            default_options={
                "timeout": CARGO_DENY_DEFAULT_TIMEOUT,
            },
            default_timeout=CARGO_DENY_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set cargo-deny-specific options.

        Args:
            timeout: Timeout in seconds (default: 60).
            **kwargs: Additional options.
        """
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(timeout=timeout)
        super().set_options(**options, **kwargs)

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run `cargo deny check` and parse results.

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
            no_files_message="No Cargo files found to check.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cargo_root = _find_cargo_root(ctx.files)
        if cargo_root is None:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No Cargo.toml found; skipping cargo-deny.",
                issues_count=0,
            )

        cmd = _build_cargo_deny_command()

        try:
            success_cmd, output = run_subprocess_with_timeout(
                tool=self,
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=str(cargo_root),
                tool_name="cargo-deny",
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=ctx.timeout,
                cmd=cmd,
                tool_name="cargo-deny",
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=timeout_result.issues_count,
                issues=timeout_result.issues,
            )

        issues = parse_cargo_deny_output(output=output)
        issues_count = len(issues)

        # cargo-deny returns non-zero on any issues found
        # Consider it successful if we parsed output correctly
        return ToolResult(
            name=self.definition.name,
            success=issues_count == 0,
            output=None,
            issues_count=issues_count,
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """cargo-deny cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Raises:
            NotImplementedError: cargo-deny does not support fixing issues.
        """
        raise NotImplementedError(
            "cargo-deny cannot automatically fix issues. Run 'lintro check' to "
            "see issues and resolve them manually.",
        )
