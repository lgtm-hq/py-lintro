"""Gitleaks tool definition.

Gitleaks is a SAST tool for detecting and preventing hardcoded secrets like
passwords, API keys, and tokens in git repos. It scans for patterns that match
known secret formats and reports findings with detailed location information.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - used safely with shell disabled
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.gitleaks.gitleaks_parser import parse_gitleaks_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_positive_int,
    validate_str,
)

# Constants for Gitleaks configuration
GITLEAKS_DEFAULT_TIMEOUT: int = 60
GITLEAKS_DEFAULT_PRIORITY: int = 90  # High priority for security tool
GITLEAKS_FILE_PATTERNS: list[str] = ["*"]  # Scans all files
GITLEAKS_OUTPUT_FORMAT: str = "json"


@register_tool
@dataclass
class GitleaksPlugin(BaseToolPlugin):
    """Gitleaks secret detection plugin.

    This plugin integrates Gitleaks with Lintro for detecting hardcoded
    secrets like passwords, API keys, and tokens in source code.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="gitleaks",
            description=(
                "SAST tool for detecting hardcoded secrets like passwords, "
                "API keys, and tokens in git repos"
            ),
            can_fix=False,
            tool_type=ToolType.SECURITY,
            file_patterns=GITLEAKS_FILE_PATTERNS,
            priority=GITLEAKS_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".gitleaks.toml"],
            version_command=["gitleaks", "version"],
            min_version=get_min_version(ToolName.GITLEAKS),
            default_options={
                "timeout": GITLEAKS_DEFAULT_TIMEOUT,
                "no_git": True,  # Default to scanning files without git history
                "config": None,
                "baseline_path": None,
                "redact": True,
                "max_target_megabytes": None,
            },
            default_timeout=GITLEAKS_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        no_git: bool | None = None,
        config: str | None = None,
        baseline_path: str | None = None,
        redact: bool | None = None,
        max_target_megabytes: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Gitleaks-specific options.

        Args:
            no_git: Scan without git history (files only).
            config: Path to gitleaks config file.
            baseline_path: Path to baseline file (ignore known secrets).
            redact: Redact secrets in output.
            max_target_megabytes: Skip files larger than this size.
            **kwargs: Other tool options.
        """
        validate_bool(value=no_git, name="no_git")
        validate_str(value=config, name="config")
        validate_str(value=baseline_path, name="baseline_path")
        validate_bool(value=redact, name="redact")
        validate_positive_int(value=max_target_megabytes, name="max_target_megabytes")

        options = filter_none_options(
            no_git=no_git,
            config=config,
            baseline_path=baseline_path,
            redact=redact,
            max_target_megabytes=max_target_megabytes,
        )
        super().set_options(**options, **kwargs)

    def _build_check_command(self, source_path: str, report_path: str) -> list[str]:
        """Build the gitleaks check command.

        Args:
            source_path: Path to the directory or file to scan.
            report_path: Path to write the JSON report to.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["gitleaks", "detect"]

        # Source path
        cmd.extend(["--source", source_path])

        # Scan without git history by default
        if self.options.get("no_git", True):
            cmd.append("--no-git")

        # Config file
        config_opt = self.options.get("config")
        if config_opt is not None:
            cmd.extend(["--config", str(config_opt)])

        # Baseline file
        baseline_opt = self.options.get("baseline_path")
        if baseline_opt is not None:
            cmd.extend(["--baseline-path", str(baseline_opt)])

        # Redact secrets
        if self.options.get("redact", True):
            cmd.append("--redact")

        # Max target megabytes
        max_mb_opt = self.options.get("max_target_megabytes")
        if max_mb_opt is not None:
            cmd.extend(["--max-target-megabytes", str(max_mb_opt)])

        # Output format and path
        cmd.extend(["--report-format", GITLEAKS_OUTPUT_FORMAT])
        cmd.extend(["--report-path", report_path])

        # Exit with code 0 even when secrets are found (we parse the output)
        cmd.append("--exit-code")
        cmd.append("0")

        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return Gitleaks documentation URL.

        Args:
            code: Gitleaks rule ID (e.g., "aws-access-key-id").

        Returns:
            URL to the Gitleaks repository, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.GITLEAKS

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Gitleaks for hardcoded secrets.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation
        ctx = self._prepare_execution(paths=paths, options=options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Determine source path based on provided paths
        # Gitleaks can scan both directories and individual files
        cwd_path = Path(ctx.cwd) if ctx.cwd else Path.cwd()
        if paths and len(paths) == 1:
            # Single path provided - use it directly
            source_path = paths[0]
        elif paths and len(paths) > 1:
            # Multiple paths - resolve relative to ctx.cwd and find common parent
            resolved_paths = [
                str(Path(p) if Path(p).is_absolute() else cwd_path / p) for p in paths
            ]
            try:
                source_path = str(Path(os.path.commonpath(resolved_paths)))
            except ValueError:
                # Paths on different drives or no common path - fall back to cwd
                logger.warning(
                    "Cannot determine common path for provided paths; "
                    "falling back to working directory.",
                )
                source_path = str(cwd_path)
        else:
            # No paths provided - fall back to cwd
            source_path = str(cwd_path)

        # Use a temporary file for the report (gitleaks can't write to /dev/stdout
        # in subprocess environments due to permission issues)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as report_file:
            report_path = report_file.name

        try:
            cmd = self._build_check_command(
                source_path=source_path,
                report_path=report_path,
            )
            logger.debug(
                f"[gitleaks] Running: {' '.join(cmd[:10])}... (cwd={ctx.cwd})",
            )

            output: str
            execution_failure: bool = False
            try:
                # Note: gitleaks with --exit-code 0 always returns success,
                # we parse the JSON output to determine findings
                self._run_subprocess(
                    cmd=cmd,
                    timeout=ctx.timeout,
                    cwd=ctx.cwd,
                )
                # Read the report from the temp file
                output = Path(report_path).read_text(encoding="utf-8").strip()
            except subprocess.TimeoutExpired:
                timeout_msg = (
                    f"Gitleaks execution timed out ({ctx.timeout}s limit exceeded)."
                    "\n\nThis may indicate:\n"
                    "  - Large codebase taking too long to scan\n"
                    "  - Need to increase timeout via --tool-options gitleaks:timeout=N"
                )
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=timeout_msg,
                    issues_count=0,
                )
            except (OSError, ValueError, RuntimeError) as e:
                logger.error(f"Failed to run Gitleaks: {e}")
                output = f"Gitleaks failed: {e}"
                execution_failure = True

            # Parse the JSON output
            if execution_failure:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=output,
                    issues_count=0,
                )

            issues = parse_gitleaks_output(output=output)
            issues_count = len(issues)

            # Check for parsing failures: if we have output that's not empty/[] but
            # got no issues, verify the output is valid JSON. This catches cases
            # where the report file contains invalid data.
            if issues_count == 0 and output and output.strip() not in ("", "[]"):
                try:
                    json.loads(output)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse gitleaks output: {e}")
                    return ToolResult(
                        name=self.definition.name,
                        success=False,
                        output=f"Failed to parse gitleaks output: {e}",
                        issues_count=0,
                    )

            return ToolResult(
                name=self.definition.name,
                success=True,
                output=None,
                issues_count=issues_count,
                issues=issues,
            )
        finally:
            # Clean up the temporary report file
            Path(report_path).unlink(missing_ok=True)

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Gitleaks cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Gitleaks does not support fixing issues.
        """
        raise NotImplementedError(
            "Gitleaks cannot automatically fix security issues. Run 'lintro check' to "
            "see issues and manually remove or rotate the detected secrets.",
        )
