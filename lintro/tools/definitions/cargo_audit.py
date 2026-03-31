"""Cargo-audit tool definition.

Cargo-audit is a security tool for Rust projects that scans Cargo.lock
for dependencies with known security vulnerabilities from the RustSec
advisory database.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.cargo_audit.cargo_audit_parser import parse_cargo_audit_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool

# Constants for cargo-audit configuration
CARGO_AUDIT_DEFAULT_TIMEOUT: int = 120  # Network operations can be slow
CARGO_AUDIT_DEFAULT_PRIORITY: int = 95  # Security scans run late
CARGO_AUDIT_FILE_PATTERNS: list[str] = ["Cargo.lock"]


def _find_cargo_root(paths: list[str]) -> Path | None:
    """Return the nearest directory containing Cargo.lock for given paths.

    Args:
        paths: List of file paths to search from.

    Returns:
        Path to directory containing Cargo.lock, or None if not found.
    """
    for raw_path in paths:
        current = Path(raw_path).resolve()
        # If it's a file, start from its parent
        if current.is_file():
            current = current.parent
        # Search upward for Cargo.lock
        for candidate in [current, *list(current.parents)]:
            lock_file = candidate / "Cargo.lock"
            if lock_file.exists():
                return candidate

    return None


@register_tool
@dataclass
class CargoAuditPlugin(BaseToolPlugin):
    """Cargo-audit plugin for Lintro.

    Provides security vulnerability scanning for Rust dependencies using
    cargo-audit to check against the RustSec advisory database.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition with cargo-audit configuration.
        """
        return ToolDefinition(
            name="cargo_audit",
            description="Security vulnerability scanner for Rust dependencies",
            can_fix=False,
            tool_type=ToolType.SECURITY,
            file_patterns=CARGO_AUDIT_FILE_PATTERNS,
            priority=CARGO_AUDIT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".cargo/audit.toml"],
            version_command=["cargo", "audit", "--version"],
            min_version=get_min_version(ToolName.CARGO_AUDIT),
            default_options={
                "timeout": CARGO_AUDIT_DEFAULT_TIMEOUT,
            },
            default_timeout=CARGO_AUDIT_DEFAULT_TIMEOUT,
        )

    def set_options(self, **kwargs: Any) -> None:
        """Set tool-specific options.

        Args:
            **kwargs: Options to set, including timeout.

        Raises:
            ValueError: If timeout is negative or not a number.
        """
        if "timeout" in kwargs:
            timeout = kwargs["timeout"]
            if timeout is not None:
                if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                    raise ValueError("timeout must be a number")
                if timeout < 0:
                    raise ValueError("timeout must be non-negative")
        super().set_options(**kwargs)

    def _build_command(self) -> list[str]:
        """Build the cargo-audit command.

        Returns:
            Command list for running cargo-audit with JSON output.
        """
        return ["cargo", "audit", "--json"]

    def doc_url(self, code: str) -> str | None:
        """Return RustSec advisory URL for the given advisory ID.

        Args:
            code: RUSTSEC advisory ID (e.g., "RUSTSEC-2021-0124").

        Returns:
            URL to the RustSec advisory page, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.CARGO_AUDIT.format(code=code)

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Rust dependencies for security vulnerabilities.

        Args:
            paths: List of paths to check.
            options: Additional options for the check.

        Returns:
            ToolResult with security scan results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            # early_result is guaranteed non-None when should_skip is True
            return ctx.early_result  # type: ignore[return-value]

        # Find Cargo.lock root from filtered file list
        cargo_root = _find_cargo_root(ctx.files)
        if cargo_root is None:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No Cargo.lock found; skipping cargo-audit.",
                issues_count=0,
            )

        cmd = self._build_command()
        try:
            success, output = self._run_subprocess(
                cmd,
                timeout=ctx.timeout,
                cwd=str(cargo_root),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"cargo-audit timed out after {ctx.timeout}s",
                issues_count=0,
            )

        issues = parse_cargo_audit_output(output)

        # Determine overall success: subprocess must succeed AND no issues found.
        # cargo-audit returns non-zero if vulnerabilities found, but also if
        # execution fails. If subprocess failed with no issues parsed, it's
        # an execution error (not a clean scan).
        overall_success = success and len(issues) == 0

        # Show output when there are issues OR when subprocess failed without
        # issues (execution error case)
        should_show_output = bool(issues) or not success

        return ToolResult(
            name=self.definition.name,
            success=overall_success,
            output=output if should_show_output else None,
            issues_count=len(issues),
            issues=issues if issues else None,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Cargo-audit cannot automatically fix vulnerabilities.

        Args:
            paths: List of paths (unused).
            options: Additional options (unused).

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Always, as cargo-audit cannot auto-fix.
        """
        raise NotImplementedError(
            "cargo-audit cannot automatically fix vulnerabilities. "
            "Update dependencies manually using `cargo update`.",
        )
