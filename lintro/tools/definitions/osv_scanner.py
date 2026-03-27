"""OSV-Scanner tool definition.

OSV-Scanner is Google's vulnerability scanner that uses the Open Source
Vulnerabilities (OSV) database. It supports scanning lockfiles and SBOMs
for known vulnerabilities across multiple ecosystems including PyPI, npm,
Go, Rust, Ruby, PHP, .NET, Java, and more.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.plugins.base import ExecutionContext

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.osv_scanner import (
    classify_suppressions,
    parse_osv_scanner_output,
    parse_suppressions,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import validate_bool, validate_positive_int

# Constants
OSV_SCANNER_DEFAULT_TIMEOUT: int = 120  # Network operations can be slow
OSV_SCANNER_DEFAULT_PRIORITY: int = 90  # High priority for security tool

# Lockfile patterns that osv-scanner recognizes.
# These are exact filenames (not source-code globs) — osv-scanner discovers
# the ecosystem from the lockfile format, so we only need to list files that
# the tool can actually parse.
OSV_SCANNER_FILE_PATTERNS: list[str] = [
    # Python
    "requirements.txt",
    "Pipfile.lock",
    "poetry.lock",
    "pdm.lock",
    "uv.lock",
    # JavaScript/TypeScript
    "package-lock.json",
    "bun.lock",
    "yarn.lock",
    "pnpm-lock.yaml",
    # Go
    "go.sum",
    # Rust
    "Cargo.lock",
    # Ruby
    "Gemfile.lock",
    # PHP
    "composer.lock",
    # .NET
    "packages.lock.json",
    # Java
    "pom.xml",
    "gradle.lockfile",
]


def _find_scan_root(paths: list[str]) -> Path | None:
    """Return the common ancestor directory for all lockfile paths.

    When multiple lockfiles are provided (e.g., requirements.txt in /app
    and package-lock.json in /app/frontend), returns their lowest common
    ancestor so osv-scanner runs from an appropriate root.

    Args:
        paths: List of file paths discovered by file discovery.

    Returns:
        Path to common ancestor directory, or None if no paths given.
    """
    parents: list[Path] = []
    for raw_path in paths:
        current = Path(raw_path).resolve()
        if current.is_file():
            parents.append(current.parent)
        elif current.is_dir():
            parents.append(current)

    if not parents:
        return None

    if len(parents) == 1:
        return parents[0]

    # Find lowest common ancestor of all lockfile directories
    try:
        return Path(os.path.commonpath([str(p) for p in parents]))
    except ValueError:
        # Different drives on Windows — fall back to first parent
        return parents[0]


@register_tool
@dataclass
class OsvScannerPlugin(BaseToolPlugin):
    """OSV-Scanner vulnerability scanning plugin.

    This plugin integrates OSV-Scanner with Lintro for scanning lockfiles
    for known vulnerabilities across multiple ecosystems.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="osv_scanner",
            description=(
                "Google's vulnerability scanner using the OSV database "
                "for multi-ecosystem dependency scanning"
            ),
            can_fix=False,
            tool_type=ToolType.SECURITY,
            file_patterns=OSV_SCANNER_FILE_PATTERNS,
            priority=OSV_SCANNER_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".osv-scanner.toml"],
            version_command=["osv-scanner", "--version"],
            min_version=get_min_version(ToolName.OSV_SCANNER),
            default_options={
                "timeout": OSV_SCANNER_DEFAULT_TIMEOUT,
                "check_suppressions": True,
            },
            default_timeout=OSV_SCANNER_DEFAULT_TIMEOUT,
        )

    def set_options(self, **kwargs: Any) -> None:
        """Set tool-specific options.

        Args:
            **kwargs: Options to set, including timeout and
                check_suppressions.
        """
        if "timeout" in kwargs:
            validate_positive_int(kwargs["timeout"], "timeout")
        if "check_suppressions" in kwargs:
            validate_bool(kwargs["check_suppressions"], "check_suppressions")
        super().set_options(**kwargs)

    def _build_command(self, lockfiles: list[str]) -> list[str]:
        """Build the osv-scanner scan command.

        Uses --lockfile for each discovered lockfile for precise scanning,
        rather than --recursive which may scan unintended files.

        Args:
            lockfiles: List of lockfile paths to scan.

        Returns:
            Command list for running osv-scanner with JSON output.
        """
        cmd = [
            *self._get_executable_command("osv-scanner"),
            "scan",
            "--format",
            "json",
        ]

        for lockfile in lockfiles:
            cmd.extend(["--lockfile", lockfile])

        return cmd

    def _build_probe_command(self, lockfiles: list[str]) -> list[str]:
        """Build an osv-scanner command that ignores all suppressions.

        Uses --config /dev/null to disable .osv-scanner.toml so the
        scan reports all vulnerabilities, including suppressed ones.
        This "probe" output is used to detect stale suppressions.

        Args:
            lockfiles: List of lockfile paths to scan.

        Returns:
            Command list for running osv-scanner without suppressions.
        """
        cmd = [
            *self._get_executable_command("osv-scanner"),
            "scan",
            "--format",
            "json",
            "--config",
            os.devnull,
        ]

        for lockfile in lockfiles:
            cmd.extend(["--lockfile", lockfile])

        return cmd

    @staticmethod
    def _find_config_file(scan_root: Path) -> Path | None:
        """Find .osv-scanner.toml by walking up from the scan root.

        Matches osv-scanner's own config resolution: looks for the file
        in the scan root and each parent directory up to the filesystem
        root.

        Args:
            scan_root: Directory to start searching from.

        Returns:
            Path to the config file, or None if not found.
        """
        current = scan_root.resolve()
        for directory in [current, *current.parents]:
            config = directory / ".osv-scanner.toml"
            if config.is_file():
                return config
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Scan lockfiles with OSV-Scanner for known vulnerabilities.

        Args:
            paths: List of file or directory paths to scan.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with scan results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Find the scan root from discovered lockfiles.
        # _prepare_execution already skips when no files match, so ctx.files
        # is guaranteed non-empty here — assert the invariant.
        scan_root = _find_scan_root(ctx.files)
        if scan_root is None:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output="No scan root found from discovered lockfiles",
                issues_count=0,
            )

        cmd = self._build_command(ctx.files)
        logger.debug(
            f"[osv-scanner] Running: {' '.join(cmd[:10])}... (cwd={scan_root})",
        )

        try:
            # osv-scanner returns non-zero when vulnerabilities exist
            success, output = self._run_subprocess(
                cmd,
                timeout=ctx.timeout,
                cwd=str(scan_root),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"OSV-Scanner timed out after {ctx.timeout}s",
                issues_count=0,
            )

        issues = parse_osv_scanner_output(output)

        # Determine overall success: subprocess must succeed AND no issues
        # found. A non-zero exit with 0 parsed issues indicates an execution
        # error (e.g. network failure), not a clean scan.
        overall_success = success and len(issues) == 0

        # Show output when there are issues OR when subprocess failed without
        # issues (execution error case)
        should_show_output = bool(issues) or not success

        # Suppression staleness check: run a probe scan without suppressions
        # to detect which suppressed vulnerabilities are still present.
        suppression_metadata = self._check_suppression_staleness(
            ctx=ctx,
            scan_root=scan_root,
            options=options,
        )

        return ToolResult(
            name=self.definition.name,
            success=overall_success,
            output=output if should_show_output else None,
            issues_count=len(issues),
            issues=issues if issues else None,
            ai_metadata=suppression_metadata,
        )

    def _check_suppression_staleness(
        self,
        ctx: ExecutionContext,
        scan_root: Path,
        options: dict[str, object],
    ) -> dict[str, Any] | None:
        """Run a probe scan to classify suppression entries.

        Skipped when check_suppressions is disabled or no config file
        with suppressions exists.

        Args:
            ctx: Execution context from _prepare_execution().
            scan_root: Root directory for the scan.
            options: Runtime options (may override self.options).

        Returns:
            Metadata dict with suppression classifications, or None.
        """
        # Runtime options override instance defaults
        check = options.get(
            "check_suppressions",
            self.options.get("check_suppressions", True),
        )
        if not check:
            return None

        config_path = self._find_config_file(scan_root)
        if config_path is None:
            return None

        entries = parse_suppressions(config_path)
        if not entries:
            return None

        # Run osv-scanner without suppressions to see all vulnerabilities
        probe_cmd = self._build_probe_command(ctx.files)
        try:
            _probe_success, probe_output = self._run_subprocess(
                probe_cmd,
                timeout=ctx.timeout,
                cwd=str(scan_root),
            )
        except subprocess.TimeoutExpired:
            logger.debug("[osv-scanner] Probe scan timed out, skipping staleness check")
            return None

        probe_issues = parse_osv_scanner_output(probe_output)

        # If probe failed and returned no parseable issues, skip classification
        # to avoid incorrectly marking all suppressions as stale.
        if not _probe_success and not probe_issues:
            logger.debug(
                "[osv-scanner] Probe scan failed with no parseable output, "
                "skipping staleness check",
            )
            return None

        probe_vuln_ids = {issue.vuln_id for issue in probe_issues}

        classified = classify_suppressions(entries, probe_vuln_ids)

        return {
            "suppressions": [
                {
                    "id": c.entry.id,
                    "ignore_until": c.entry.ignore_until.isoformat(),
                    "reason": c.entry.reason,
                    "status": c.status.value,
                }
                for c in classified
            ],
        }

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """OSV-Scanner cannot fix vulnerabilities, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: OSV-Scanner does not support fixing issues.
        """
        raise NotImplementedError(
            "OSV-Scanner cannot automatically fix vulnerabilities. "
            "Update affected packages to their fixed versions.",
        )
