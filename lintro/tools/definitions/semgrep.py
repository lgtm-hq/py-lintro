"""Semgrep tool definition.

Semgrep is a fast, open-source static analysis tool for finding bugs and
enforcing code standards. It supports 30+ languages using pattern-based rules
and is commonly used for security scanning and code quality enforcement.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.semgrep_enums import SemgrepSeverity, normalize_semgrep_severity
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.semgrep.semgrep_parser import parse_semgrep_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_list,
    validate_str,
)

# Constants for Semgrep configuration
SEMGREP_DEFAULT_TIMEOUT: int = 300  # Semgrep needs more time on larger codebases
SEMGREP_DEFAULT_PRIORITY: int = 85  # High priority for security tool
SEMGREP_FILE_PATTERNS: list[str] = [
    "*.py",
    "*.js",
    "*.ts",
    "*.jsx",
    "*.tsx",
    "*.go",
    "*.java",
    "*.rb",
    "*.php",
    "*.c",
    "*.cpp",
    "*.rs",
]
SEMGREP_OUTPUT_FORMAT: str = "json"
SEMGREP_DEFAULT_CONFIG: str = "auto"


def _extract_semgrep_json(raw_text: str) -> dict[str, Any]:
    """Extract Semgrep's JSON object from mixed stdout/stderr text.

    Semgrep may print informational lines alongside the JSON report.
    This helper locates the first opening brace and the last closing brace
    and attempts to parse the enclosed JSON object.

    Args:
        raw_text: Combined stdout+stderr text from Semgrep.

    Returns:
        Parsed JSON object.

    Raises:
        json.JSONDecodeError: If JSON cannot be parsed.
        ValueError: If no JSON object boundaries are found.
    """
    if not raw_text or not raw_text.strip():
        raise json.JSONDecodeError("Empty output", raw_text or "", 0)

    text: str = raw_text.strip()

    # Quick path: if the entire text is JSON
    if text.startswith("{") and text.endswith("}"):
        result: dict[str, Any] = json.loads(text)
        return result

    start: int = text.find("{")
    end: int = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Could not locate JSON object in Semgrep output")

    json_str: str = text[start : end + 1]
    parsed: dict[str, Any] = json.loads(json_str)
    return parsed


@register_tool
@dataclass
class SemgrepPlugin(BaseToolPlugin):
    """Semgrep static analysis and security scanning plugin.

    This plugin integrates Semgrep with Lintro for finding security
    vulnerabilities and enforcing code standards across multiple languages.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="semgrep",
            description=(
                "Fast, open-source static analysis tool for finding bugs "
                "and enforcing code standards"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.SECURITY,
            file_patterns=SEMGREP_FILE_PATTERNS,
            priority=SEMGREP_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".semgrep.yaml", ".semgrep.yml", ".semgrep/"],
            version_command=["semgrep", "--version"],
            min_version=get_min_version(ToolName.SEMGREP),
            default_options={
                "timeout": SEMGREP_DEFAULT_TIMEOUT,
                "config": SEMGREP_DEFAULT_CONFIG,
                "exclude": None,
                "include": None,
                "severity": None,
                "timeout_threshold": None,
                "jobs": None,
                "verbose": False,
                "quiet": False,
            },
            default_timeout=SEMGREP_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        config: str | None = None,
        exclude: list[str] | None = None,
        include: list[str] | None = None,
        severity: str | SemgrepSeverity | None = None,
        timeout_threshold: int | None = None,
        jobs: int | None = None,
        verbose: bool | None = None,
        quiet: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Semgrep-specific options.

        Args:
            config: Config string (auto, p/python, p/javascript, path to YAML).
            exclude: Patterns to exclude from scanning.
            include: Patterns to include in scanning.
            severity: Minimum severity level (INFO, WARNING, ERROR).
            timeout_threshold: Per-file timeout in seconds.
            jobs: Number of parallel jobs.
            verbose: Verbose output.
            quiet: Quiet mode.
            **kwargs: Other tool options.

        Raises:
            ValueError: If an option value is invalid.
        """
        validate_str(config, "config")
        validate_list(exclude, "exclude")
        validate_list(include, "include")

        severity_str: str | None = None
        if severity is not None:
            severity_str = normalize_semgrep_severity(severity).name

        if timeout_threshold is not None and (
            not isinstance(timeout_threshold, int) or timeout_threshold < 0
        ):
            raise ValueError("timeout_threshold must be a non-negative integer")

        if jobs is not None and (not isinstance(jobs, int) or jobs < 1):
            raise ValueError("jobs must be a positive integer")

        options = filter_none_options(
            config=config,
            exclude=exclude,
            include=include,
            severity=severity_str,
            timeout_threshold=timeout_threshold,
            jobs=jobs,
            verbose=verbose,
            quiet=quiet,
        )
        super().set_options(**options, **kwargs)

    def _build_check_command(self, files: list[str]) -> list[str]:
        """Build the semgrep check command.

        Args:
            files: List of files to check.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = self._get_executable_command("semgrep") + ["scan"]

        # Output format - always use JSON for reliable parsing
        cmd.extend([f"--{SEMGREP_OUTPUT_FORMAT}"])

        # Config option (required for semgrep to know what rules to use)
        config_opt = self.options.get("config", SEMGREP_DEFAULT_CONFIG)
        if config_opt is not None:
            cmd.extend(["--config", str(config_opt)])

        # Exclude patterns
        exclude_opt = self.options.get("exclude")
        if exclude_opt is not None and isinstance(exclude_opt, list):
            for pattern in exclude_opt:
                cmd.extend(["--exclude", str(pattern)])

        # Include patterns
        include_opt = self.options.get("include")
        if include_opt is not None and isinstance(include_opt, list):
            for pattern in include_opt:
                cmd.extend(["--include", str(pattern)])

        # Severity filter
        severity_opt = self.options.get("severity")
        if severity_opt is not None:
            cmd.extend(["--severity", str(severity_opt)])

        # Per-file timeout
        timeout_threshold_opt = self.options.get("timeout_threshold")
        if timeout_threshold_opt is not None:
            cmd.extend(["--timeout", str(timeout_threshold_opt)])

        # Parallel jobs
        jobs_opt = self.options.get("jobs")
        if jobs_opt is not None:
            cmd.extend(["--jobs", str(jobs_opt)])

        # Verbose/quiet flags
        if self.options.get("verbose"):
            cmd.append("--verbose")

        if self.options.get("quiet"):
            cmd.append("--quiet")

        # Add files/directories to scan
        cmd.extend(files)

        return cmd

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Semgrep for security issues and code quality.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(paths=paths, options=options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd: list[str] = self._build_check_command(files=ctx.rel_files)
        logger.debug(f"[semgrep] Running: {' '.join(cmd[:10])}... (cwd={ctx.cwd})")

        output: str
        execution_failure: bool = False
        try:
            # Note: semgrep returns non-zero exit code when findings exist,
            # so we intentionally ignore the success return value
            _, combined = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
            output = (combined or "").strip()
        except subprocess.TimeoutExpired:
            timeout_msg = (
                f"Semgrep execution timed out ({ctx.timeout}s limit exceeded).\n\n"
                "This may indicate:\n"
                "  - Large codebase taking too long to process\n"
                "  - Need to increase timeout via --tool-options semgrep:timeout=N"
            )
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=timeout_msg,
                issues_count=0,
            )
        except (OSError, ValueError, RuntimeError) as e:
            logger.error(f"Failed to run Semgrep: {e}")
            output = f"Semgrep failed: {e}"
            execution_failure = True

        # Parse the JSON output
        try:
            if ("{" not in output or "}" not in output) and execution_failure:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=output,
                    issues_count=0,
                )

            semgrep_data = _extract_semgrep_json(raw_text=output)
            json_output = json.dumps(semgrep_data)
            issues = parse_semgrep_output(output=json_output)
            issues_count = len(issues)

            # Check for errors in the response
            # Partial parsing errors (e.g., TypeScript 4.9+ 'satisfies' keyword)
            # are warnings, not fatal errors. Only fail on actual errors.
            errors = semgrep_data.get("errors", [])
            fatal_errors = [e for e in errors if e.get("level", "error") == "error"]

            def _is_partial_parsing(err: dict[str, Any]) -> bool:
                """Check if error is a PartialParsing warning.

                Semgrep's error type can be either a string or a list where
                the first element is the error type name.
                """
                if err.get("level") != "warn":
                    return False
                err_type = err.get("type")
                if isinstance(err_type, str):
                    return err_type == "PartialParsing"
                if isinstance(err_type, list) and len(err_type) > 0:
                    return str(err_type[0]) == "PartialParsing"
                return False

            parsing_warnings = [e for e in errors if _is_partial_parsing(e)]

            # Log parsing warnings but don't fail
            if parsing_warnings:
                logger.warning(
                    "[semgrep] {} file(s) partially parsed (may use unsupported "
                    "syntax like TypeScript 4.9+ 'satisfies')",
                    len(parsing_warnings),
                )

            execution_success = len(fatal_errors) == 0 and not execution_failure
            has_fatal_errors = execution_failure or len(fatal_errors) > 0

            return ToolResult(
                name=self.definition.name,
                success=execution_success,
                output=output if has_fatal_errors else None,
                issues_count=issues_count,
                issues=issues,
            )

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse semgrep output: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(output or f"Failed to parse semgrep output: {str(e)}"),
                issues_count=0,
            )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Semgrep cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Semgrep does not support fixing issues.
        """
        raise NotImplementedError(
            "Semgrep cannot automatically fix security issues. Run 'lintro check' to "
            "see issues.",
        )
