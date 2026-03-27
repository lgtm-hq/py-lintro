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
from typing import Any

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
from lintro.plugins.execution_preparation import (
    get_effective_timeout,
    verify_tool_version,
)
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import validate_bool, validate_positive_int

# Constants
OSV_SCANNER_DEFAULT_TIMEOUT: int = 120  # Network operations can be slow
OSV_SCANNER_DEFAULT_PRIORITY: int = 90  # High priority for security tool


@register_tool
@dataclass
class OsvScannerPlugin(BaseToolPlugin):
    """OSV-Scanner vulnerability scanning plugin.

    This plugin integrates OSV-Scanner with Lintro for scanning lockfiles
    for known vulnerabilities across multiple ecosystems.

    Unlike other tool plugins, osv-scanner handles its own file discovery
    via --recursive, so file_patterns is empty and check() bypasses the
    standard file discovery pipeline.
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
            file_patterns=[],
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

    def _build_command(self, scan_root: Path) -> list[str]:
        """Build the osv-scanner scan command.

        Uses --recursive to let osv-scanner discover lockfiles itself,
        rather than maintaining a separate list of file patterns.

        Args:
            scan_root: Root directory to scan recursively.

        Returns:
            Command list for running osv-scanner with JSON output.
        """
        return [
            *self._get_executable_command("osv-scanner"),
            "scan",
            "--recursive",
            "--format",
            "json",
            str(scan_root),
        ]

    def _build_probe_command(self, scan_root: Path) -> list[str]:
        """Build an osv-scanner command that ignores all suppressions.

        Uses --config /dev/null to disable .osv-scanner.toml so the
        scan reports all vulnerabilities, including suppressed ones.
        This "probe" output is used to detect stale suppressions.

        Args:
            scan_root: Root directory to scan recursively.

        Returns:
            Command list for running osv-scanner without suppressions.
        """
        return [
            *self._get_executable_command("osv-scanner"),
            "scan",
            "--recursive",
            "--format",
            "json",
            "--config",
            os.devnull,
            str(scan_root),
        ]

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

    def _resolve_scan_root(self, paths: list[str]) -> Path:
        """Resolve the scan root from input paths.

        Args:
            paths: Input file or directory paths.

        Returns:
            Common ancestor directory for all paths.
        """
        resolved: list[Path] = []
        for raw_path in paths:
            p = Path(raw_path).resolve()
            resolved.append(p if p.is_dir() else p.parent)

        if len(resolved) == 1:
            return resolved[0]

        try:
            return Path(os.path.commonpath([str(p) for p in resolved]))
        except ValueError:
            return resolved[0]

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Scan for known vulnerabilities using osv-scanner --recursive.

        Bypasses the standard file discovery pipeline since osv-scanner
        discovers lockfiles itself. Only does version checking and
        options merging before running the scan.

        Args:
            paths: List of file or directory paths to scan.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with scan results.
        """
        if not paths:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No paths to check.",
                issues_count=0,
            )

        # Version check
        version_result = verify_tool_version(self.definition)
        if version_result is not None:
            return version_result

        # Merge options
        merged_options = dict(self.options)
        merged_options.update(options)
        timeout = get_effective_timeout(
            timeout=None,
            options=merged_options,
            default_timeout=self.definition.default_timeout,
        )

        scan_root = self._resolve_scan_root(paths)
        cmd = self._build_command(scan_root)
        logger.debug(
            f"[osv-scanner] Running: {' '.join(cmd[:10])}... (cwd={scan_root})",
        )

        try:
            # osv-scanner returns non-zero when vulnerabilities exist
            success, output = self._run_subprocess(
                cmd,
                timeout=timeout,
                cwd=str(scan_root),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"OSV-Scanner timed out after {timeout}s",
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

        # Suppression staleness check
        suppression_metadata = self._check_suppression_staleness(
            scan_root=scan_root,
            timeout=timeout,
            options=merged_options,
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
        scan_root: Path,
        timeout: float,
        options: dict[str, object],
    ) -> dict[str, Any] | None:
        """Run a probe scan to classify suppression entries.

        Skipped when check_suppressions is disabled or no config file
        with suppressions exists.

        Args:
            scan_root: Root directory for the scan.
            timeout: Timeout for subprocess execution.
            options: Merged runtime options.

        Returns:
            Metadata dict with suppression classifications, or None.
        """
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
        probe_cmd = self._build_probe_command(scan_root)
        try:
            _probe_success, probe_output = self._run_subprocess(
                probe_cmd,
                timeout=timeout,
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
