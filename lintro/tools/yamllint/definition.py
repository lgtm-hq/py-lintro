"""Yamllint tool definition.

Yamllint is a linter for YAML files that checks for syntax validity,
key duplications, and cosmetic problems such as lines length, trailing spaces,
indentation, etc.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from loguru import logger

from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_type import ToolType
from lintro.enums.yamllint_format import (
    YamllintFormat,
    normalize_yamllint_format,
)
from lintro.models.core.claim import Claim
from lintro.models.core.tool_outcome import Checked, NoFiles, TimedOut, ToolOutcome
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
from lintro.utils.option_paths import option_safe_path
from lintro.utils.path_utils import find_file_upward

# Constants for Yamllint configuration
YAMLLINT_DEFAULT_TIMEOUT: int = 15
YAMLLINT_FILE_PATTERNS: list[str] = [
    "*.yml",
    "*.yaml",
    ".yamllint",
    ".yamllint.yml",
    ".yamllint.yaml",
]
YAMLLINT_FORMATS: tuple[str, ...] = tuple(m.name.lower() for m in YamllintFormat)
# ANSI underline that yamllint's ``colored`` format wraps each file header in.
_COLORED_HEADER: tuple[str, str] = ("\x1b[4m", "\x1b[0m")


def _show_path(output: str, *, passed: str, shown: str) -> str:
    """Replace the path yamllint was given with the path lintro reports.

    yamllint prints a file exactly as it was passed. Each of its formats puts
    that path in fixed places, and only those places are rewritten:
    ``parsable`` starts each problem line with ``path:``; ``standard`` (and
    ``auto`` without a terminal) prints ``path`` alone as a header;
    ``colored`` underlines that header; ``github`` opens ``::group::path``
    and names ``file=path,`` in each annotation.

    Args:
        output: Raw yamllint output for one file.
        passed: The path given to yamllint.
        shown: The path to show instead.

    Returns:
        The output with every file reference naming ``shown``.
    """
    if passed == shown:
        return output
    start, end = _COLORED_HEADER
    exact = {
        passed: shown,
        f"{start}{passed}{end}": f"{start}{shown}{end}",
        f"::group::{passed}": f"::group::{shown}",
    }
    prefixes = [
        (f"{passed}:", f"{shown}:"),
        (f"::error file={passed},", f"::error file={shown},"),
        (f"::warning file={passed},", f"::warning file={shown},"),
    ]
    lines = []
    for line in output.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        if body in exact:
            body = exact[body]
        else:
            for old, new in prefixes:
                if body.startswith(old):
                    body = new + body[len(old) :]
                    break
        lines.append(body + newline)
    return "".join(lines)


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
            claims=[
                Claim(
                    patterns=YAMLLINT_FILE_PATTERNS,
                    capabilities={Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=True,
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

    def set_options(
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

        start_dir = Path(search_dir).absolute() if search_dir else Path.cwd()
        found = find_file_upward(start_dir, config_paths)
        if found is not None:
            logger.debug(
                f"[YamllintPlugin] Found config file: {found} "
                f"(searched from {start_dir})",
            )
            return str(found)

        return None

    def _config_invocation(self, abs_file: str) -> tuple[list[str], str, str]:
        """Choose the config arguments, working directory and path for a file.

        yamllint matches ``ignore`` patterns (top-level and per rule) against
        the path exactly as passed, so an absolute path never matches them.
        A config file passed explicitly is used as ``yamllint -c`` would be
        from lintro's working directory: yamllint runs there on a path
        relative to it. A discovered config sits next to the files it governs,
        so yamllint runs from that config's directory instead. Either way
        yamllint decides itself which rules a file is exempt from.

        Args:
            abs_file: Absolute path of the YAML file to lint.

        Returns:
            The ``--config-file`` arguments (empty without a config), the
            directory to run in, and the path to pass to yamllint.
        """
        file_dir = os.path.dirname(abs_file)
        config_file = self._find_yamllint_config(search_dir=file_dir)
        if not config_file:
            return [], file_dir, abs_file
        abs_config_file = os.path.abspath(config_file)
        logger.debug(
            f"[YamllintPlugin] Using config file: {abs_config_file} "
            f"(original: {config_file})",
        )
        config_args = ["--config-file", abs_config_file]
        run_dir = (
            os.getcwd()
            if self.options.get("config_file")
            else os.path.dirname(abs_config_file)
        )
        try:
            # A name such as ``@x.yaml`` stays a path (#3584).
            relative = option_safe_path(os.path.relpath(abs_file, run_dir))
            return config_args, run_dir, relative
        except ValueError:
            # Different drive on Windows: no relative path exists.
            return config_args, file_dir, abs_file

    def _process_single_file(
        self,
        file_path: str,
        timeout: float,
        results: dict[str, Any],
    ) -> None:
        """Process a single YAML file with yamllint.

        Args:
            file_path: Path to the YAML file to process.
            timeout: Timeout in seconds for the subprocess call.
            results: Dictionary to accumulate results across files.
        """
        abs_file: str = os.path.abspath(file_path)

        # Build command
        cmd: list[str] = self._get_executable_command(tool_name="yamllint")
        format_option = str(self.options.get("format", YAMLLINT_FORMATS[0]))
        cmd.extend(["--format", format_option])

        config_args, run_dir, target = self._config_invocation(abs_file)
        cmd.extend(config_args)

        config_data_opt = self.options.get("config_data")
        if config_data_opt:
            cmd.extend(["--config-data", str(config_data_opt)])
        if self.options.get("strict", False):
            cmd.append("--strict")
        if self.options.get("relaxed", False):
            cmd.append("--relaxed")
        if self.options.get("no_warnings", False):
            cmd.append("--no-warnings")

        # The target may be relative to run_dir, so a name such as
        # ``-config.yaml`` would otherwise be read as an option.
        cmd.extend(["--", target])
        logger.debug(f"[YamllintPlugin] Processing file: {abs_file}")
        logger.debug(f"[YamllintPlugin] Command: {' '.join(cmd)} (cwd={run_dir})")

        try:
            success, output = self._run_subprocess(
                cmd=cmd,
                timeout=timeout,
                cwd=run_dir,
            )
            # Report the same absolute path whichever directory yamllint ran
            # in, in the raw output of every format and in parsed issues.
            output = _show_path(output, passed=target, shown=abs_file)
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
        normalized = code.strip() if code else ""
        if normalized:
            return DocUrlTemplate.YAMLLINT.format(code=normalized)
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
        ctx = self.prepare(
            paths,
            merged_options,
            no_files_message="No files to check.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

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

        if not yaml_files:
            return ToolResult.from_outcome(
                ToolOutcome(execution=NoFiles()),
                name=self.definition.name,
                output="No YAML files found to check.",
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

        return ToolResult.from_outcome(
            ToolOutcome(
                execution=(
                    TimedOut(combined_output)
                    if results["timeout_count"] > 0
                    else Checked(success=results["all_success"], inspected_files=True)
                ),
                issues_count=results["total_issues"],
            ),
            name=self.definition.name,
            output=combined_output,
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
