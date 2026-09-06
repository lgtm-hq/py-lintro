"""Prettier tool definition.

Prettier is an opinionated code formatter for CSS, HTML, JSON, YAML, Markdown,
GraphQL, and Astro. JavaScript/TypeScript files are handled by oxfmt for better
performance. Prettier enforces a consistent code style by parsing and
re-printing code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.prettier.prettier_issue import PrettierIssue
from lintro.parsers.prettier.prettier_parser import parse_prettier_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.batch_runner import (
    BatchCheckPolicy,
    BatchFixPolicy,
    BatchSuccess,
    run_batch_check,
    run_batch_fix,
)
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_positive_int,
)
from lintro.utils.path_utils import find_file_upward

# Constants for Prettier configuration
PRETTIER_DEFAULT_TIMEOUT: int = 120
PRETTIER_DEFAULT_PRIORITY: int = 80
# Note: JS/TS/Vue files are handled by oxfmt (faster).
# Prettier handles file types that oxfmt doesn't support.
PRETTIER_CONFIG_FILENAMES: tuple[str, ...] = (
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.json5",
    ".prettierrc.yaml",
    ".prettierrc.yml",
    ".prettierrc.js",
    ".prettierrc.cjs",
    ".prettierrc.mjs",
    ".prettierrc.toml",
    "prettier.config.js",
    "prettier.config.cjs",
    "prettier.config.mjs",
    "prettier.config.ts",
    "prettier.config.cts",
    "prettier.config.mts",
)
PRETTIER_FILE_PATTERNS: list[str] = [
    "*.css",
    "*.scss",
    "*.less",
    "*.html",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.md",
    "*.graphql",
    "*.astro",
]


@register_tool
@dataclass
class PrettierPlugin(BaseToolPlugin):
    """Prettier code formatter plugin.

    This plugin integrates Prettier with Lintro for formatting CSS, HTML,
    JSON, YAML, Markdown, GraphQL, and Astro files. JS/TS files are handled by oxfmt.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="prettier",
            description=(
                "Code formatter for CSS, HTML, JSON, YAML, Markdown, GraphQL, "
                "and Astro (JS/TS handled by oxfmt for better performance)"
            ),
            can_fix=True,
            tool_type=ToolType.FORMATTER,
            file_patterns=PRETTIER_FILE_PATTERNS,
            priority=PRETTIER_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=list(PRETTIER_CONFIG_FILENAMES),
            version_command=["prettier", "--version"],
            min_version=get_min_version(ToolName.PRETTIER),
            default_options={
                "timeout": PRETTIER_DEFAULT_TIMEOUT,
                "verbose_fix_output": False,
                "line_length": None,
            },
            default_timeout=PRETTIER_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        verbose_fix_output: bool | None = None,
        line_length: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Prettier-specific options.

        Args:
            verbose_fix_output: If True, include raw Prettier output in fix().
            line_length: Print width for prettier (maps to --print-width).
            **kwargs: Other tool options.
        """
        validate_bool(verbose_fix_output, "verbose_fix_output")
        validate_positive_int(line_length, "line_length")

        options = filter_none_options(
            verbose_fix_output=verbose_fix_output,
            line_length=line_length,
        )
        super().set_options(**options, **kwargs)

    def _find_prettier_config(self, search_dir: str | None = None) -> str | None:
        """Locate prettier config file by walking up the directory tree.

        Prettier searches upward from the file's directory to find config files,
        so we do the same to match native behavior and ensure config is found
        even when cwd is a subdirectory.

        Args:
            search_dir: Directory to start searching from. If None, searches from
                current working directory.

        Returns:
            str | None: Path to config file if found, None otherwise.
        """
        config_paths = [*PRETTIER_CONFIG_FILENAMES, "package.json"]
        # Search upward from search_dir (or cwd) to find config, just like prettier
        start_dir = Path(search_dir).absolute() if search_dir else Path.cwd()

        # Walk upward using the shared helper. ``package.json`` is existence-only
        # to the helper, so its prettier-content sniffing is layered on top: a
        # ``package.json`` without a ``prettier`` key does not count as config, so
        # the search resumes above the directory that contained it.
        current = start_dir
        while True:
            found = find_file_upward(current, config_paths)
            if found is None:
                return None

            if found.name == "package.json" and not self._package_json_has_prettier(
                found,
            ):
                parent = found.parent.parent
                if parent == found.parent:
                    # Reached the filesystem root without a usable config.
                    return None
                current = parent
                continue

            logger.debug(
                f"[PrettierPlugin] Found config file: {found} "
                f"(searched from {start_dir})",
            )
            return str(found)

    @staticmethod
    def _package_json_has_prettier(path: Path) -> bool:
        """Report whether a package.json declares a ``prettier`` key.

        Args:
            path: Path to the package.json file to inspect.

        Returns:
            True if the file parses as JSON and contains a top-level
            ``prettier`` key, False otherwise (including unreadable or
            invalid files).
        """
        try:
            with path.open(encoding="utf-8") as f:
                pkg_data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, PermissionError):
            return False
        return "prettier" in pkg_data

    def _find_prettierignore(self, search_dir: str | None = None) -> str | None:
        """Locate .prettierignore file by walking up the directory tree.

        Prettier searches upward from the file's directory to find .prettierignore,
        so we do the same to match native behavior.

        Args:
            search_dir: Directory to start searching from. If None, searches from
                current working directory.

        Returns:
            str | None: Path to .prettierignore file if found, None otherwise.
        """
        start_dir = Path(search_dir).absolute() if search_dir else Path.cwd()
        found = find_file_upward(start_dir, [".prettierignore"])
        if found is not None:
            logger.debug(
                f"[PrettierPlugin] Found .prettierignore: {found} "
                f"(searched from {start_dir})",
            )
            return str(found)

        return None

    def _create_not_found_result(
        self,
        cwd: str | None = None,
    ) -> ToolResult:
        """Create a ToolResult for when Prettier is not found.

        Args:
            cwd: Working directory for the tool result.

        Returns:
            ToolResult: ToolResult instance representing Prettier not found.
        """
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=(
                "Prettier not found.\n\n"
                "Please ensure prettier is installed:\n"
                "  - Run 'npm install -g prettier' or 'bun add -g prettier'\n"
                "  - Or install locally: 'npm install prettier'"
            ),
            issues_count=0,
            cwd=cwd,
        )

    def _execution_error_result(
        self,
        *,
        exc: Exception,
        cwd: str | None = None,
    ) -> ToolResult:
        """Create a ToolResult for a Prettier invocation that could not run.

        Args:
            exc: Exception raised while launching or running Prettier.
            cwd: Working directory for the tool result.

        Returns:
            ToolResult: Not-found guidance when the binary is missing, and a
            generic execution failure otherwise.
        """
        if isinstance(exc, FileNotFoundError):
            return self._create_not_found_result(cwd=cwd)
        logger.error(f"Failed to run prettier: {exc}")
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=f"Prettier execution failed: {exc}",
            issues_count=0,
            cwd=cwd,
        )

    def _create_timeout_result(
        self,
        timeout_val: int,
        initial_issues: list[PrettierIssue] | None = None,
        initial_count: int = 0,
        cwd: str | None = None,
    ) -> ToolResult:
        """Create a ToolResult for timeout scenarios.

        Follows the shared timeout accounting model (see
        :mod:`lintro.tools.core.timeout_utils`): the timeout is reported via
        ``timed_out=True`` and ``success=False`` rather than as a synthetic
        ``TIMEOUT`` pseudo-issue, so it never inflates the issue counts. Only
        genuine issues detected before the timeout are reported.

        Args:
            timeout_val: The timeout value that was exceeded.
            initial_issues: Optional list of issues found before timeout.
            initial_count: Optional count of initial issues.
            cwd: Working directory for the tool result.

        Returns:
            ToolResult: ToolResult instance representing timeout failure.
        """
        timeout_msg = (
            f"Prettier execution timed out ({timeout_val}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options prettier:timeout=N"
        )
        detected_issues = list(initial_issues or [])
        remaining_count = len(detected_issues)
        # Maintain invariant: initial = fixed + remaining
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=timeout_msg,
            issues_count=remaining_count,
            issues=detected_issues,
            initial_issues_count=remaining_count,
            fixed_issues_count=0,
            remaining_issues_count=remaining_count,
            cwd=cwd,
            timed_out=True,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Prettier without making changes.

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

        logger.debug(
            f"[PrettierPlugin] Discovered {len(ctx.files)} files matching patterns: "
            f"{self.definition.file_patterns}",
        )
        logger.debug(
            f"[PrettierPlugin] Exclude patterns applied: {self.exclude_patterns}",
        )
        if ctx.files:
            logger.debug(
                f"[PrettierPlugin] Files to check (first 10): {ctx.files[:10]}",
            )
        logger.debug(f"[PrettierPlugin] Working directory: {ctx.cwd}")

        # Resolve executable in a manner consistent with other tools
        cmd: list[str] = self._get_executable_command(
            tool_name="prettier",
            cwd=ctx.cwd,
        ) + [
            "--check",
        ]

        # Add Lintro config injection args (--no-config, --config)
        config_args = self._build_config_args()
        if config_args:
            cmd.extend(config_args)
            logger.debug("[PrettierPlugin] Using Lintro config injection")
        else:
            # Fallback: Find config and ignore files by walking up from cwd
            found_config = self._find_prettier_config(search_dir=ctx.cwd)
            if found_config:
                logger.debug(
                    f"[PrettierPlugin] Found config: {found_config} (auto-detecting)",
                )
            else:
                logger.debug(
                    "[PrettierPlugin] No prettier config file found (using defaults)",
                )
                # Apply line_length as --print-width if set and no config found
                line_length = self.options.get("line_length")
                if line_length:
                    cmd.extend(["--print-width", str(line_length)])
                    logger.debug(
                        "[PrettierPlugin] Using --print-width=%s from options",
                        line_length,
                    )
            # Find .prettierignore by walking up from cwd
            prettierignore_path = self._find_prettierignore(search_dir=ctx.cwd)
            if prettierignore_path:
                logger.debug(
                    f"[PrettierPlugin] Found .prettierignore: {prettierignore_path} "
                    "(auto-detecting)",
                )

        cmd.extend(ctx.rel_files)
        logger.debug(f"[PrettierPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")

        # Standardize: suppress Prettier's informational output when no issues.
        # Prettier exits non-zero purely to report unformatted files, so the
        # parsed list — not the exit status — is the verdict.
        return run_batch_check(
            ctx,
            plugin=self,
            cmd=cmd,
            parse=lambda output: parse_prettier_output(output=output),
            policy=BatchCheckPolicy(success=BatchSuccess.ISSUES_ONLY),
            cwd=ctx.cwd,
            result_cwd=ctx.cwd,
            on_timeout=lambda: self._create_timeout_result(
                timeout_val=ctx.timeout,
                cwd=ctx.cwd,
            ),
            on_error=lambda exc: self._execution_error_result(
                exc=exc,
                cwd=ctx.cwd,
            ),
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Format files with Prettier.

        Args:
            paths: List of file or directory paths to format.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Result object with counts and messages.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Use shared preparation for version check, path validation, file discovery
        ctx = self.prepare(
            paths,
            merged_options,
            no_files_message="No files to format.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        # Get Lintro config injection args (--no-config, --config)
        config_args = self._build_config_args()
        fallback_args: list[str] = []
        if not config_args:
            # Fallback: Find config and ignore files by walking up from cwd
            found_config = self._find_prettier_config(search_dir=ctx.cwd)
            if found_config:
                logger.debug(
                    f"[PrettierPlugin] Found config: {found_config} (auto-detecting)",
                )
            else:
                logger.debug(
                    "[PrettierPlugin] No prettier config file found (using defaults)",
                )
                # Apply line_length as --print-width if set and no config found
                line_length = self.options.get("line_length")
                if line_length:
                    fallback_args.extend(["--print-width", str(line_length)])
                    logger.debug(
                        "[PrettierPlugin] Using --print-width=%s from options",
                        line_length,
                    )
            prettierignore_path = self._find_prettierignore(search_dir=ctx.cwd)
            if prettierignore_path:
                logger.debug(
                    f"[PrettierPlugin] Found .prettierignore: {prettierignore_path} "
                    "(auto-detecting)",
                )

        # Check for issues first
        check_cmd: list[str] = self._get_executable_command(
            tool_name="prettier",
            cwd=ctx.cwd,
        ) + [
            "--check",
        ]
        if config_args:
            check_cmd.extend(config_args)
        elif fallback_args:
            check_cmd.extend(fallback_args)
        check_cmd.extend(ctx.rel_files)
        logger.debug(
            f"[PrettierPlugin] Checking: {' '.join(check_cmd)} (cwd={ctx.cwd})",
        )

        fix_cmd: list[str] = self._get_executable_command(
            tool_name="prettier",
            cwd=ctx.cwd,
        ) + [
            "--write",
        ]
        if config_args:
            fix_cmd.extend(config_args)
        elif fallback_args:
            fix_cmd.extend(fallback_args)
        fix_cmd.extend(ctx.rel_files)
        logger.debug(f"[PrettierPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")

        return run_batch_fix(
            ctx,
            plugin=self,
            check_cmd=check_cmd,
            fix_cmd=fix_cmd,
            parse=lambda output: parse_prettier_output(output=output),
            policy=BatchFixPolicy(
                fixed_label="formatting issue",
                all_fixed_message=(
                    "All formatting issues were successfully auto-fixed"
                ),
                verbose_output_label="Formatting output",
                verbose=bool(self.options.get("verbose_fix_output", False)),
                report_initial_issues=True,
            ),
            cwd=ctx.cwd,
            result_cwd=ctx.cwd,
            on_timeout=lambda detected: self._create_timeout_result(
                timeout_val=ctx.timeout,
                initial_issues=list(detected) or None,
                initial_count=len(detected),
                cwd=ctx.cwd,
            ),
            on_error=lambda exc: self._execution_error_result(
                exc=exc,
                cwd=ctx.cwd,
            ),
        )
