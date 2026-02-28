"""Yamllint tool definition.

Yamllint is a linter for YAML files that checks for syntax validity,
key duplications, and cosmetic problems such as lines length, trailing spaces,
indentation, etc.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

import click
from loguru import logger

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from lintro.enums.tool_type import ToolType
from lintro.enums.yamllint_format import (
    YamllintFormat,
    normalize_yamllint_format,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.yamllint.yamllint_parser import parse_yamllint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_str,
)

# Constants for Yamllint configuration
YAMLLINT_DEFAULT_TIMEOUT: int = 15
YAMLLINT_DEFAULT_PRIORITY: int = 40
YAMLLINT_FILE_PATTERNS: list[str] = [
    "*.yml",
    "*.yaml",
    ".yamllint",
    ".yamllint.yml",
    ".yamllint.yaml",
]
YAMLLINT_FORMATS: tuple[str, ...] = tuple(m.name.lower() for m in YamllintFormat)


@register_tool
@dataclass
class YamllintPlugin(BaseToolPlugin):
    """Yamllint YAML linter plugin.

    This plugin integrates Yamllint with Lintro for checking YAML files
    for syntax errors and style issues.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="yamllint",
            description="YAML linter for syntax and style checking",
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=YAMLLINT_FILE_PATTERNS,
            priority=YAMLLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".yamllint", ".yamllint.yml", ".yamllint.yaml"],
            version_command=["yamllint", "--version"],
            min_version="1.26.0",
            default_options={
                "timeout": YAMLLINT_DEFAULT_TIMEOUT,
                "format": "parsable",
                "config_file": None,
                "config_data": None,
                "strict": False,
                "relaxed": False,
                "no_warnings": False,
            },
            default_timeout=YAMLLINT_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        format: str | YamllintFormat | None = None,
        config_file: str | None = None,
        config_data: str | None = None,
        strict: bool | None = None,
        relaxed: bool | None = None,
        no_warnings: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Yamllint-specific options.

        Args:
            format: Output format (parsable, standard, colored, github, auto).
            config_file: Path to yamllint config file.
            config_data: Inline config data (YAML string).
            strict: Return non-zero exit code on warnings as well as errors.
            relaxed: Use relaxed configuration.
            no_warnings: Output only error level problems.
            **kwargs: Other tool options.
        """
        # Normalize format enum if provided
        if format is not None:
            fmt_enum = normalize_yamllint_format(format)
            format = fmt_enum.name.lower()

        validate_str(config_file, "config_file")
        validate_str(config_data, "config_data")
        validate_bool(strict, "strict")
        validate_bool(relaxed, "relaxed")
        validate_bool(no_warnings, "no_warnings")

        options = filter_none_options(
            format=format,
            config_file=config_file,
            config_data=config_data,
            strict=strict,
            relaxed=relaxed,
            no_warnings=no_warnings,
        )
        super().set_options(**options, **kwargs)

    def _find_yamllint_config(self, search_dir: str | None = None) -> str | None:
        """Locate yamllint config file if not explicitly provided.

        Yamllint searches upward from the file's directory to find config files,
        so we do the same to match native behavior.

        Args:
            search_dir: Directory to start searching from. If None, searches from
                current working directory.

        Returns:
            str | None: Path to config file if found, None otherwise.
        """
        # If config_file is explicitly set, use it
        config_file = self.options.get("config_file")
        if config_file:
            return str(config_file)

        # If config_data is set, don't search for config file
        if self.options.get("config_data"):
            return None

        # Check for config files in order of precedence
        config_paths = [
            ".yamllint",
            ".yamllint.yml",
            ".yamllint.yaml",
        ]

        start_dir = os.path.abspath(search_dir) if search_dir else os.getcwd()
        current_dir = start_dir

        while True:
            for config_name in config_paths:
                config_path = os.path.join(current_dir, config_name)
                if os.path.exists(config_path):
                    logger.debug(
                        f"[YamllintPlugin] Found config file: {config_path} "
                        f"(searched from {start_dir})",
                    )
                    return config_path

            parent_dir = os.path.dirname(current_dir)
            if parent_dir == current_dir:
                break
            current_dir = parent_dir

        return None

    def _load_yamllint_ignore_patterns(
        self,
        config_file: str | None,
    ) -> list[str]:
        """Load ignore patterns from yamllint config file.

        Args:
            config_file: Path to yamllint config file, or None.

        Returns:
            list[str]: List of ignore patterns from the config file.
        """
        if not config_file or not os.path.exists(config_file):
            return []

        ignore_patterns: list[str] = []
        if yaml is None:
            logger.debug(
                "[YamllintPlugin] PyYAML not available, cannot parse ignore patterns",
            )
            return ignore_patterns

        try:
            with open(config_file, encoding="utf-8") as f:
                config_data = yaml.safe_load(f)
                if config_data and isinstance(config_data, dict):
                    # Check for ignore patterns in line-length rule
                    line_length_config = config_data.get("rules", {}).get(
                        "line-length",
                        {},
                    )
                    if isinstance(line_length_config, dict):
                        ignore_value = line_length_config.get("ignore")
                        if ignore_value:
                            if isinstance(ignore_value, str):
                                ignore_patterns.extend(
                                    [
                                        line.strip()
                                        for line in ignore_value.split("\n")
                                        if line.strip()
                                    ],
                                )
                            elif isinstance(ignore_value, list):
                                ignore_patterns.extend(ignore_value)
                    logger.debug(
                        f"[YamllintPlugin] Loaded {len(ignore_patterns)} ignore "
                        f"patterns from {config_file}: {ignore_patterns}",
                    )
        except (OSError, ValueError, KeyError, yaml.YAMLError) as e:
            logger.debug(
                f"[YamllintPlugin] Failed to load ignore patterns "
                f"from {config_file}: {e}",
            )

        return ignore_patterns

    def _should_ignore_file(
        self,
        file_path: str,
        ignore_patterns: list[str],
    ) -> bool:
        """Check if a file should be ignored based on yamllint ignore patterns.

        Args:
            file_path: Path to the file to check.
            ignore_patterns: List of ignore patterns from yamllint config.

        Returns:
            bool: True if the file should be ignored, False otherwise.
        """
        if not ignore_patterns:
            return False

        normalized_path: str = file_path.replace("\\", "/")

        for pattern in ignore_patterns:
            pattern = pattern.strip()
            if not pattern:
                continue
            if normalized_path.startswith(pattern):
                return True
            if f"/{pattern}" in normalized_path:
                return True
            if fnmatch.fnmatch(normalized_path, pattern):
                return True

        return False

    def _process_single_file(
        self,
        file_path: str,
        timeout: int,
        results: dict[str, Any],
    ) -> None:
        """Process a single YAML file with yamllint.

        Args:
            file_path: Path to the YAML file to process.
            timeout: Timeout in seconds for the subprocess call.
            results: Dictionary to accumulate results across files.
        """
        abs_file: str = os.path.abspath(file_path)
        file_dir: str = os.path.dirname(abs_file)

        # Build command
        cmd: list[str] = self._get_executable_command(tool_name="yamllint")
        format_option = str(self.options.get("format", YAMLLINT_FORMATS[0]))
        cmd.extend(["--format", format_option])

        # Discover config file relative to the file being checked
        config_file: str | None = self._find_yamllint_config(search_dir=file_dir)
        if config_file:
            abs_config_file = os.path.abspath(config_file)
            cmd.extend(["--config-file", abs_config_file])
            logger.debug(
                f"[YamllintPlugin] Using config file: {abs_config_file} "
                f"(original: {config_file})",
            )

        config_data_opt = self.options.get("config_data")
        if config_data_opt:
            cmd.extend(["--config-data", str(config_data_opt)])
        if self.options.get("strict", False):
            cmd.append("--strict")
        if self.options.get("relaxed", False):
            cmd.append("--relaxed")
        if self.options.get("no_warnings", False):
            cmd.append("--no-warnings")

        cmd.append(abs_file)
        logger.debug(f"[YamllintPlugin] Processing file: {abs_file}")
        logger.debug(f"[YamllintPlugin] Command: {' '.join(cmd)}")

        try:
            success, output = self._run_subprocess(
                cmd=cmd,
                timeout=timeout,
                cwd=file_dir,
            )
            issues = parse_yamllint_output(output=output)
            issues_count = len(issues)

            if not success:
                results["all_success"] = False
            results["total_issues"] += issues_count

            # Store raw output when there are issues OR when execution failed
            # This ensures error messages are visible even if parsing fails
            if output and (issues or not success):
                results["all_outputs"].append(output)
            if issues:
                results["all_issues"].extend(issues)
        except subprocess.TimeoutExpired:
            results["skipped_files"].append(file_path)
            results["all_success"] = False
            results["timeout_count"] += 1
        except FileNotFoundError:
            # File not found - skip silently
            pass
        except OSError as e:
            import errno

            if e.errno not in (errno.ENOENT, errno.ENOTDIR):
                logger.debug(f"Yamllint execution error for {file_path}: {e}")
                results["all_success"] = False
                results["execution_failures"] += 1
        except (ValueError, RuntimeError) as e:
            logger.debug(f"Yamllint execution error for {file_path}: {e}")
            results["all_success"] = False
            results["execution_failures"] += 1

    def doc_url(self, code: str) -> str | None:
        """Return yamllint documentation URL for the given rule.

        Args:
            code: Yamllint rule name (e.g., "line-length").

        Returns:
            URL to the yamllint rule documentation.
        """
        if code:
            return f"https://yamllint.readthedocs.io/en/stable/rules.html#{code}"
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Yamllint.

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
        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No files to check.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        yaml_files = ctx.files

        logger.debug(
            f"[YamllintPlugin] Discovered {len(yaml_files)} files matching patterns: "
            f"{self.definition.file_patterns}",
        )
        logger.debug(
            f"[YamllintPlugin] Exclude patterns applied: {self.exclude_patterns}",
        )
        if yaml_files:
            logger.debug(
                f"[YamllintPlugin] Files to check (first 10): {yaml_files[:10]}",
            )

        # Load ignore patterns from yamllint config
        config_file = self._find_yamllint_config(
            search_dir=paths[0] if paths else None,
        )
        ignore_patterns = self._load_yamllint_ignore_patterns(config_file=config_file)

        # Filter files based on ignore patterns
        if ignore_patterns:
            original_count = len(yaml_files)
            yaml_files = [
                f
                for f in yaml_files
                if not self._should_ignore_file(
                    file_path=f,
                    ignore_patterns=ignore_patterns,
                )
            ]
            filtered_count = original_count - len(yaml_files)
            if filtered_count > 0:
                logger.debug(
                    f"[YamllintPlugin] Filtered out {filtered_count} files based on "
                    f"yamllint ignore patterns: {ignore_patterns}",
                )

        if not yaml_files:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No YAML files found to check.",
                issues_count=0,
            )

        # Accumulate results across all files
        results: dict[str, Any] = {
            "all_outputs": [],
            "all_issues": [],
            "all_success": True,
            "skipped_files": [],
            "timeout_count": 0,
            "execution_failures": 0,
            "total_issues": 0,
        }

        # Show progress bar only when processing multiple files
        if len(yaml_files) >= 2:
            with click.progressbar(
                yaml_files,
                label="Processing files",
                bar_template="%(label)s  %(info)s",
            ) as bar:
                for file_path in bar:
                    self._process_single_file(file_path, ctx.timeout, results)
        else:
            for file_path in yaml_files:
                self._process_single_file(file_path, ctx.timeout, results)

        # Build combined output from all collected outputs
        combined_output = (
            "\n".join(results["all_outputs"]) if results["all_outputs"] else None
        )

        # Append timeout/failure messages if any
        if results["timeout_count"] > 0:
            timeout_msg = (
                f"Skipped {results['timeout_count']} file(s) due to timeout "
                f"({ctx.timeout}s limit exceeded):"
            )
            for file in results["skipped_files"]:
                timeout_msg += f"\n  - {file}"
            combined_output = (
                f"{combined_output}\n\n{timeout_msg}"
                if combined_output
                else timeout_msg
            )

        if results["execution_failures"] > 0:
            failure_msg = (
                f"Failed to process {results['execution_failures']} file(s) "
                "due to execution errors"
            )
            combined_output = (
                f"{combined_output}\n\n{failure_msg}"
                if combined_output
                else failure_msg
            )

        return ToolResult(
            name=self.definition.name,
            success=results["all_success"],
            output=combined_output,
            issues_count=results["total_issues"],
            issues=results["all_issues"],
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Yamllint cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths (unused).
            options: Runtime options (unused).

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Yamllint does not support fixing issues.
        """
        raise NotImplementedError(
            "Yamllint cannot automatically fix issues. Use a YAML formatter "
            "or manually fix the reported issues.",
        )
