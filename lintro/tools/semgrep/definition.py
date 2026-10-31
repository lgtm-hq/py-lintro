"""Semgrep tool definition.

Semgrep is a fast, open-source static analysis tool for finding bugs and
enforcing code standards. It supports 30+ languages using pattern-based rules
and is commonly used for security scanning and code quality enforcement.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote as url_quote

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.semgrep_enums import SemgrepSeverity, normalize_semgrep_severity
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.semgrep.semgrep_parser import parse_semgrep_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.rules import resolve_rule_pack, validate_rule_packs
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_list,
    validate_str,
)

# Constants for Semgrep configuration
SEMGREP_DEFAULT_TIMEOUT: int = 300  # Semgrep needs more time on larger codebases
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


def _rule_configs(
    options: dict[str, object],
    cwd: str | None,
    config_explicit: bool,
) -> list[str]:
    """Combine explicit or native Semgrep configuration with packaged rules.

    Args:
        options: Validated plugin options.
        cwd: Scan project root, used to locate native configuration.
        config_explicit: Whether the caller explicitly selected a configuration.

    Returns:
        Configuration arguments, preserving the default when no pack is enabled.
    """
    packs = validate_rule_packs(options.get("rule_packs", []))
    config = options.get("config", SEMGREP_DEFAULT_CONFIG)
    configs = [str(config)] if config is not None else []
    if packs and config == SEMGREP_DEFAULT_CONFIG and not config_explicit:
        # An opt-in pack replaces implicit registry access, retaining local rules.
        root = Path(cwd) if cwd is not None else Path.cwd()
        configs = [
            str(root / native)
            for native in (".semgrep.yaml", ".semgrep.yml", ".semgrep")
            if (root / native).exists()
        ]
    configs.extend(str(resolve_rule_pack(name)) for name in packs)
    return configs


def _restore_pack_rule_ids(data: dict[str, Any], packs: list[str]) -> None:
    """Remove installation-path prefixes only from marked packaged findings.

    Args:
        data: Semgrep report to normalize before parsing issue objects.
        packs: Enabled packaged rules, whose metadata identifies their findings.
    """
    results = data.get("results")
    if not isinstance(results, list):
        return
    for result in results:
        if not isinstance(result, dict):
            continue
        extra = result.get("extra")
        metadata = extra.get("metadata") if isinstance(extra, dict) else None
        pack = metadata.get("lintro_rule_pack") if isinstance(metadata, dict) else None
        check_id = result.get("check_id")
        if (
            not isinstance(pack, str)
            or pack not in packs
            or not isinstance(check_id, str)
        ):
            continue
        namespace = "lintro." + pack.replace("/", ".") + "."
        prefix, marker, suffix = check_id.rpartition(namespace)
        if marker and (not prefix or prefix.endswith(".")):
            result["check_id"] = marker + suffix


def _has_unclosed_array(prefix: str) -> bool:
    """Check for an array still open before the report's first brace.

    Args:
        prefix: Text preceding the report object.

    Returns:
        Whether an opening bracket remains outside quoted array strings.
    """
    depth = 0
    in_string = False
    escaped = False
    for char in prefix:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"' and depth:
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]" and depth:
            depth -= 1
    return depth > 0


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
        ValueError: If no JSON object boundaries are found or an array wraps it.
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

    # Balanced status prefixes are allowed; complete or truncated array wrappers
    # leave an opening bracket before the object and must not be sliced away.
    if _has_unclosed_array(text[:start]):
        raise ValueError("Semgrep output must be a JSON object, got an array")

    json_str: str = text[start : end + 1]
    parsed: dict[str, Any] = json.loads(json_str)
    return parsed


def _extract_semgrep_errors(data: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Extract error objects and count malformed containers or entries.

    Args:
        data: Parsed Semgrep report.

    Returns:
        Valid error objects and the number of error parse failures.
    """
    raw_errors = data.get("errors", [])
    if not isinstance(raw_errors, list):
        return [], 1
    errors = [error for error in raw_errors if isinstance(error, dict)]
    return errors, len(raw_errors) - len(errors)


@register_tool
@dataclass
class SemgrepPlugin(BaseToolPlugin):
    """Semgrep static analysis and security scanning plugin.

    This plugin integrates Semgrep with Lintro for finding security
    vulnerabilities and enforcing code standards across multiple languages.
    """

    _config_explicit: bool = field(default=False, init=False, repr=False)

    def _reset_execution_state(self) -> None:
        """Restore implicit configuration selection when options are reset."""
        self._config_explicit = False

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        # Generic script rules include Makefiles and extensionless scripts.
        # Discovery matches basenames only; let the pack's paths.include apply
        # its precise directory globs after opt-in instead of losing those files.
        packs = validate_rule_packs(self.options.get("rule_packs", []))
        file_patterns = (
            ["*"] if "scripts/no-direct-formatter" in packs else SEMGREP_FILE_PATTERNS
        )
        return ToolDefinition(
            name="semgrep",
            description=(
                "Fast, open-source static analysis tool for finding bugs "
                "and enforcing code standards"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.SECURITY,
            file_patterns=file_patterns,
            claims=[
                Claim(
                    patterns=file_patterns,
                    capabilities={Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=True,
            native_configs=[".semgrep.yaml", ".semgrep.yml", ".semgrep/"],
            version_command=["semgrep", "--version"],
            min_version=get_min_version(ToolName.SEMGREP),
            default_options={
                "timeout": SEMGREP_DEFAULT_TIMEOUT,
                "config": SEMGREP_DEFAULT_CONFIG,
                "rule_packs": [],
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
        timeout_threshold: object = None,
        jobs: object = None,
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
            **kwargs: Other tool options, including ``rule_packs`` (a list of
                packaged language/pack names, listed by ``list-tools --rules``).

        Raises:
            ValueError: If an option value is invalid.
        """
        validate_str(config, "config")
        validate_list(exclude, "exclude")
        validate_list(include, "include")
        if "rule_packs" in kwargs:
            kwargs["rule_packs"] = validate_rule_packs(kwargs["rule_packs"])

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
        if config is not None:
            self._config_explicit = True

    def _build_check_command(
        self,
        files: list[str],
        cwd: str | None = None,
    ) -> list[str]:
        """Build the semgrep check command.

        Args:
            files: List of files to check.
            cwd: Scan project root for native configuration discovery.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = self._get_executable_command("semgrep") + ["scan"]

        # Output format - always use JSON for reliable parsing
        cmd.extend([f"--{SEMGREP_OUTPUT_FORMAT}"])

        # Config option (required for semgrep to know what rules to use)
        for config in _rule_configs(self.options, cwd, self._config_explicit):
            cmd.extend(["--config", config])

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

    def doc_url(self, code: str) -> str | None:
        """Return Semgrep registry URL for the given rule ID.

        Registry rule IDs use dotted notation (e.g.,
        ``python.lang.security.insecure-random``). Custom rules loaded
        from local files typically contain ``/`` and are not in the
        public registry.

        Args:
            code: Semgrep rule ID.

        Returns:
            URL to the Semgrep registry page, or None if the rule is
            empty or appears to be a local/custom rule.
        """
        if not code or code.startswith("lintro."):
            return None
        if "." in code and "/" not in code:
            return DocUrlTemplate.SEMGREP.format(code=url_quote(code, safe=""))
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Semgrep for security issues and code quality.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.

        """
        if options:
            runtime_options: dict[str, Any] = dict(options)
            self.set_options(**runtime_options)
        # Use shared preparation for version check, path validation, file discovery
        ctx = self.prepare(paths=paths, options=options)
        if isinstance(ctx, ToolResult):
            return ctx

        cmd: list[str] = self._build_check_command(files=ctx.rel_files, cwd=ctx.cwd)
        logger.debug(f"[semgrep] Running: {' '.join(cmd[:10])}... (cwd={ctx.cwd})")

        output: str
        returncode: int | None = None
        execution_failure: bool = False
        try:
            proc = self._run_subprocess_result(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
            # Findings normally exit 0; accept exit 1 with findings for compatibility.
            returncode = proc.returncode
            execution_failure = returncode not in (0, 1)
            output = proc.output.strip()
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
                timed_out=True,
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
            _restore_pack_rule_ids(
                semgrep_data,
                validate_rule_packs(self.options.get("rule_packs", [])),
            )
            json_output = json.dumps(semgrep_data)
            issues = parse_semgrep_output(output=json_output)
            issues_count = len(issues)
            errors, error_parse_failures = _extract_semgrep_errors(semgrep_data)
            # The parser emits one issue per usable result; account for every drop.
            parse_failures_count = (
                len(semgrep_data["results"]) - issues_count + error_parse_failures
            )
            execution_failure = execution_failure or (
                returncode == 1 and issues_count == 0
            )

            # Check for errors in the response
            # Partial parsing errors (e.g., TypeScript 4.9+ 'satisfies' keyword)
            # are warnings, not fatal errors. Only fail on actual errors.
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

            execution_success = not (
                fatal_errors or execution_failure or parse_failures_count
            )
            reported_paths = semgrep_data.get("paths")
            scanned = (
                reported_paths.get("scanned")
                if isinstance(reported_paths, dict)
                else None
            )
            # Parsed findings prove inspection even when reported paths are empty.
            inspected_files = (
                bool(issues or scanned)
                if isinstance(scanned, list)
                and all(isinstance(p, str) and p for p in scanned)
                else True if issues else None
            )

            return ToolResult(
                name=self.definition.name,
                inspected_files=inspected_files,
                success=execution_success,
                output=output if not execution_success else None,
                issues_count=issues_count,
                issues=issues,
                parse_failures_count=parse_failures_count or None,
            )

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse semgrep output: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(output or f"Failed to parse semgrep output: {str(e)}"),
                issues_count=0,
                parse_failures_count=1,
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
