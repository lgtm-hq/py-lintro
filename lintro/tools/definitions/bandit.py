"""Bandit tool definition.

Bandit is a security linter designed to find common security issues in Python code.
It processes Python files, builds an AST, and runs security plugins against the
AST nodes to identify potential vulnerabilities.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro.enums.bandit_levels import (
    BanditConfidenceLevel,
    BanditSeverityLevel,
    normalize_bandit_confidence_level,
    normalize_bandit_severity_level,
)
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.bandit.bandit_parser import parse_bandit_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.utils.config import load_bandit_config

# Constants for Bandit configuration
BANDIT_DEFAULT_TIMEOUT: int = 30
BANDIT_DEFAULT_PRIORITY: int = 90  # High priority for security tool
BANDIT_FILE_PATTERNS: list[str] = ["*.py", "*.pyi"]
BANDIT_OUTPUT_FORMAT: str = "json"

# Mapping of Bandit test IDs to their documentation page slugs.
# Bandit doc URLs use the format: plugins/<slug>.html
BANDIT_PLUGIN_SLUGS: dict[str, str] = {
    "B101": "b101_assert_used",
    "B102": "b102_exec_used",
    "B103": "b103_set_bad_file_permissions",
    "B104": "b104_hardcoded_bind_all_interfaces",
    "B105": "b105_hardcoded_password_string",
    "B106": "b106_hardcoded_password_funcarg",
    "B107": "b107_hardcoded_password_default",
    "B108": "b108_hardcoded_tmp_directory",
    "B109": "b109_password_config_option_not_marked_secret",
    "B110": "b110_try_except_pass",
    "B111": "b111_execute_with_run_as_root_equals_true",
    "B112": "b112_try_except_continue",
    "B113": "b113_request_without_timeout",
    "B201": "b201_flask_debug_true",
    "B202": "b202_tarfile_unsafe_members",
    "B324": "b324_hashlib",
    "B501": "b501_request_with_no_cert_validation",
    "B502": "b502_ssl_with_bad_version",
    "B503": "b503_ssl_with_bad_defaults",
    "B504": "b504_ssl_with_no_version",
    "B505": "b505_weak_cryptographic_key",
    "B506": "b506_yaml_load",
    "B507": "b507_ssh_no_host_key_verification",
    "B508": "b508_snmp_insecure_version",
    "B509": "b509_snmp_weak_cryptography",
    "B601": "b601_paramiko_calls",
    "B602": "b602_subprocess_popen_with_shell_equals_true",
    "B603": "b603_subprocess_without_shell_equals_true",
    "B604": "b604_any_other_function_with_shell_equals_true",
    "B605": "b605_start_process_with_a_shell",
    "B606": "b606_start_process_with_no_shell",
    "B607": "b607_start_process_with_partial_path",
    "B608": "b608_hardcoded_sql_expressions",
    "B609": "b609_linux_commands_wildcard_injection",
    "B610": "b610_django_extra_used",
    "B611": "b611_django_rawsql_used",
    "B612": "b612_logging_config_insecure_listen",
    "B613": "b613_trojansource",
    "B614": "b614_pytorch_load",
    "B615": "b615_huggingface_unsafe_download",
    "B701": "b701_jinja2_autoescape_false",
    "B702": "b702_use_of_mako_templates",
    "B703": "b703_django_mark_safe",
    "B704": "b704_markupsafe_markup_xss",
}


def _extract_bandit_json(raw_text: str) -> dict[str, Any]:
    """Extract Bandit's JSON object from mixed stdout/stderr text.

    Bandit may print informational lines and a progress bar alongside the
    JSON report. This helper locates the first opening brace and the last
    closing brace and attempts to parse the enclosed JSON object.

    Args:
        raw_text: Combined stdout+stderr text from Bandit.

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
        raise ValueError("Could not locate JSON object in Bandit output")

    json_str: str = text[start : end + 1]
    parsed: dict[str, Any] = json.loads(json_str)
    return parsed


@register_tool
@dataclass
class BanditPlugin(BaseToolPlugin):
    """Bandit security linter plugin.

    This plugin integrates Bandit with Lintro for finding common security
    issues in Python code.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="bandit",
            description=(
                "Security linter that finds common security issues in Python code"
            ),
            can_fix=False,
            tool_type=ToolType.SECURITY,
            file_patterns=BANDIT_FILE_PATTERNS,
            priority=BANDIT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["pyproject.toml", ".bandit", "bandit.yaml"],
            version_command=["bandit", "--version"],
            min_version="1.7.0",
            default_options={
                "timeout": BANDIT_DEFAULT_TIMEOUT,
                "severity": None,
                "confidence": None,
                "tests": None,
                "skips": None,
                "profile": None,
                "configfile": None,
                "baseline": None,
                "ignore_nosec": False,
                "aggregate": "vuln",
                "verbose": False,
                "quiet": False,
            },
            default_timeout=BANDIT_DEFAULT_TIMEOUT,
        )

    def __post_init__(self) -> None:
        """Initialize the tool with configuration from pyproject.toml."""
        super().__post_init__()

        # Load bandit configuration from pyproject.toml
        bandit_config = load_bandit_config()

        # Apply configuration overrides
        if "exclude_dirs" in bandit_config:
            exclude_dirs = bandit_config["exclude_dirs"]
            if isinstance(exclude_dirs, list):
                for exclude_dir in exclude_dirs:
                    pattern = f"{exclude_dir}/*"
                    if pattern not in self.exclude_patterns:
                        self.exclude_patterns.append(pattern)
                    recursive_pattern = f"{exclude_dir}/**/*"
                    if recursive_pattern not in self.exclude_patterns:
                        self.exclude_patterns.append(recursive_pattern)

        # Set other options from configuration
        config_mapping = {
            "tests": "tests",
            "skips": "skips",
            "profile": "profile",
            "configfile": "configfile",
            "baseline": "baseline",
            "ignore_nosec": "ignore_nosec",
            "aggregate": "aggregate",
            "severity": "severity",
            "confidence": "confidence",
        }

        for config_key, option_key in config_mapping.items():
            if config_key in bandit_config:
                value = bandit_config[config_key]
                if config_key == "severity" and value is not None:
                    value = normalize_bandit_severity_level(value).value
                elif config_key == "confidence" and value is not None:
                    value = normalize_bandit_confidence_level(value).value
                elif config_key in ("skips", "tests") and isinstance(value, list):
                    value = ",".join(value)
                self.options[option_key] = value

    def set_options(  # type: ignore[override]
        self,
        severity: str | None = None,
        confidence: str | None = None,
        tests: str | None = None,
        skips: str | None = None,
        profile: str | None = None,
        configfile: str | None = None,
        baseline: str | None = None,
        ignore_nosec: bool | None = None,
        aggregate: str | None = None,
        verbose: bool | None = None,
        quiet: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Bandit-specific options.

        Args:
            severity: Minimum severity level (LOW, MEDIUM, HIGH).
            confidence: Minimum confidence level (LOW, MEDIUM, HIGH).
            tests: Comma-separated list of test IDs to run.
            skips: Comma-separated list of test IDs to skip.
            profile: Profile to use.
            configfile: Path to config file.
            baseline: Path to baseline report for comparison.
            ignore_nosec: Ignore # nosec comments.
            aggregate: Aggregate by vulnerability or file.
            verbose: Verbose output.
            quiet: Quiet mode.
            **kwargs: Other tool options.

        Raises:
            ValueError: If an option value is invalid.
        """
        if severity is not None:
            severity = normalize_bandit_severity_level(severity).value

        if confidence is not None:
            confidence = normalize_bandit_confidence_level(confidence).value

        if aggregate is not None:
            valid_aggregates = ["vuln", "file"]
            if aggregate not in valid_aggregates:
                raise ValueError(f"aggregate must be one of {valid_aggregates}")

        options: dict[str, Any] = {
            "severity": severity,
            "confidence": confidence,
            "tests": tests,
            "skips": skips,
            "profile": profile,
            "configfile": configfile,
            "baseline": baseline,
            "ignore_nosec": ignore_nosec,
            "aggregate": aggregate,
            "verbose": verbose,
            "quiet": quiet,
        }
        options = {k: v for k, v in options.items() if v is not None}
        super().set_options(**options, **kwargs)

    def _build_check_command(self, files: list[str]) -> list[str]:
        """Build the bandit check command.

        Args:
            files: List of files to check.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = self._get_executable_command("bandit") + ["-r"]

        severity_opt = self.options.get("severity")
        if severity_opt is not None:
            severity = normalize_bandit_severity_level(str(severity_opt))
            if severity == BanditSeverityLevel.LOW:
                cmd.append("-l")
            elif severity == BanditSeverityLevel.MEDIUM:
                cmd.extend(["-ll"])
            elif severity == BanditSeverityLevel.HIGH:
                cmd.extend(["-lll"])

        confidence_opt = self.options.get("confidence")
        if confidence_opt is not None:
            confidence = normalize_bandit_confidence_level(str(confidence_opt))
            if confidence == BanditConfidenceLevel.LOW:
                cmd.append("-i")
            elif confidence == BanditConfidenceLevel.MEDIUM:
                cmd.extend(["-ii"])
            elif confidence == BanditConfidenceLevel.HIGH:
                cmd.extend(["-iii"])

        tests_opt = self.options.get("tests")
        if tests_opt is not None:
            cmd.extend(["-t", str(tests_opt)])

        skips_opt = self.options.get("skips")
        if skips_opt is not None:
            cmd.extend(["-s", str(skips_opt)])

        profile_opt = self.options.get("profile")
        if profile_opt is not None:
            cmd.extend(["-p", str(profile_opt)])

        configfile_opt = self.options.get("configfile")
        if configfile_opt is not None:
            cmd.extend(["-c", str(configfile_opt)])

        baseline_opt = self.options.get("baseline")
        if baseline_opt is not None:
            cmd.extend(["-b", str(baseline_opt)])

        if self.options.get("ignore_nosec"):
            cmd.append("--ignore-nosec")

        aggregate_opt = self.options.get("aggregate")
        if aggregate_opt is not None:
            cmd.extend(["-a", str(aggregate_opt)])

        if self.options.get("verbose"):
            cmd.append("-v")

        if self.options.get("quiet"):
            cmd.append("-q")

        # Output format
        cmd.extend(["-f", BANDIT_OUTPUT_FORMAT])

        # Add quiet flag to suppress log messages that interfere with JSON parsing
        if "-q" not in cmd:
            cmd.append("-q")

        # Add files
        cmd.extend(files)

        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return Bandit documentation URL for the given code.

        Args:
            code: Bandit code (e.g., "B101").

        Returns:
            URL to the Bandit plugin documentation, or None if unknown.
        """
        if not code:
            return None
        slug = BANDIT_PLUGIN_SLUGS.get(code.upper())
        if slug:
            return f"https://bandit.readthedocs.io/en/latest/plugins/{slug}.html"
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Bandit for security issues.

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
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Use absolute paths to avoid running from inside Python package directories.
        # When bandit runs from inside lintro/, it may trigger imports that corrupt
        # the JSON output with loguru messages.
        cmd: list[str] = self._build_check_command(files=ctx.files)
        logger.debug(f"[bandit] Running: {' '.join(cmd[:10])}...")

        output: str
        stderr_output: str = ""
        execution_failure: bool = False
        try:
            # Run subprocess directly to capture stdout and stderr separately.
            # Bandit outputs JSON to stdout, but stderr may contain info/warning
            # messages that would corrupt JSON parsing if combined.
            result = subprocess.run(  # nosec B603 - cmd is validated
                cmd,
                capture_output=True,
                text=True,
                timeout=ctx.timeout,
                # Don't set cwd - use absolute paths instead to avoid
                # running from inside Python package directories
            )
            # Use only stdout for JSON parsing
            output = (result.stdout or "").strip()
            stderr_output = (result.stderr or "").strip()
            # Log stderr for debugging if present
            if stderr_output:
                logger.debug(f"[bandit] stderr: {stderr_output[:500]}")
        except subprocess.TimeoutExpired:
            timeout_msg = (
                f"Bandit execution timed out ({ctx.timeout}s limit exceeded).\n\n"
                "This may indicate:\n"
                "  - Large codebase taking too long to process\n"
                "  - Need to increase timeout via --tool-options bandit:timeout=N"
            )
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=timeout_msg,
                issues_count=0,
            )
        except (OSError, ValueError, RuntimeError) as e:
            logger.error(f"Failed to run Bandit: {e}")
            output = f"Bandit failed: {e}"
            execution_failure = True

        # Parse the JSON output
        try:
            # Handle "no files found" case - bandit outputs this to stderr, not stdout
            if (
                "No .py/.pyi files found" in output
                or "No .py/.pyi files found" in stderr_output
            ):
                logger.debug("[bandit] No Python files found to check")
                return ToolResult(
                    name=self.definition.name,
                    success=True,
                    output="No .py/.pyi files found to check.",
                    issues_count=0,
                )

            if ("{" not in output or "}" not in output) and execution_failure:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=output,
                    issues_count=0,
                )

            # Handle empty output (no JSON content to parse)
            if not output:
                # If Bandit exited non-zero with no output, treat as failure
                if result.returncode != 0:
                    return ToolResult(
                        name=self.definition.name,
                        success=False,
                        output=stderr_output or "Bandit failed with non-zero exit code",
                        issues_count=0,
                    )
                logger.debug("[bandit] Empty output received")
                return ToolResult(
                    name=self.definition.name,
                    success=True,
                    output="Bandit ran successfully and found no issues",
                    issues_count=0,
                )

            bandit_data = _extract_bandit_json(raw_text=output)
            issues = parse_bandit_output(bandit_data)
            issues_count = len(issues)

            execution_success = (
                len(bandit_data.get("errors", [])) == 0 and not execution_failure
            )

            return ToolResult(
                name=self.definition.name,
                success=execution_success,
                output=output if execution_failure else None,
                issues_count=issues_count,
                issues=issues,
            )

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse bandit output: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(output or f"Failed to parse bandit output: {str(e)}"),
                issues_count=0,
            )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Bandit cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Bandit does not support fixing issues.
        """
        raise NotImplementedError(
            "Bandit cannot automatically fix security issues. Run 'lintro check' to "
            "see issues.",
        )
