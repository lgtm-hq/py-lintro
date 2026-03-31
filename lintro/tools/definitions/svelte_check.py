"""Svelte-check tool definition.

Svelte-check is the official type checker and linter for Svelte components.
It provides TypeScript type checking, unused CSS detection, and accessibility
hints for `.svelte` files.

Example:
    # Check Svelte project
    lintro check src/ --tools svelte-check

    # Check with specific threshold
    lintro check src/ --tools svelte-check \
        --tool-options "svelte-check:threshold=warning"
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.svelte_check.svelte_check_parser import parse_svelte_check_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.timeout_utils import create_timeout_result

# Constants for Svelte-check configuration
SVELTE_CHECK_DEFAULT_TIMEOUT: int = 120
SVELTE_CHECK_DEFAULT_PRIORITY: int = 83  # After tsc (82)
SVELTE_CHECK_FILE_PATTERNS: list[str] = ["*.svelte"]


@register_tool
@dataclass
class SvelteCheckPlugin(BaseToolPlugin):
    """Svelte-check type checking plugin.

    This plugin integrates svelte-check with Lintro for static type checking
    and linting of Svelte components.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="svelte-check",
            description="Svelte type checker and linter for Svelte components",
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.TYPE_CHECKER,
            file_patterns=SVELTE_CHECK_FILE_PATTERNS,
            priority=SVELTE_CHECK_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[
                "svelte.config.js",
                "svelte.config.ts",
                "svelte.config.mjs",
            ],
            version_command=self._get_svelte_check_command() + ["--version"],
            min_version=get_min_version(ToolName.SVELTE_CHECK),
            default_options={
                "timeout": SVELTE_CHECK_DEFAULT_TIMEOUT,
                "threshold": "error",  # error, warning, or hint
                "tsconfig": None,
            },
            default_timeout=SVELTE_CHECK_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        threshold: str | None = None,
        tsconfig: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set svelte-check-specific options.

        Args:
            threshold: Minimum severity to report ("error", "warning", "hint").
            tsconfig: Path to tsconfig.json file.
            **kwargs: Other tool options.

        Raises:
            ValueError: If any provided option is of an unexpected type.
        """
        if threshold is not None:
            if not isinstance(threshold, str):
                raise ValueError("threshold must be a string")
            if threshold not in ("error", "warning", "hint"):
                raise ValueError("threshold must be 'error', 'warning', or 'hint'")
        if tsconfig is not None and not isinstance(tsconfig, str):
            raise ValueError("tsconfig must be a string path")

        options: dict[str, object] = {
            "threshold": threshold,
            "tsconfig": tsconfig,
        }
        options = {k: v for k, v in options.items() if v is not None}
        super().set_options(**options, **kwargs)

    def _get_svelte_check_command(self) -> list[str]:
        """Get the command to run svelte-check.

        Prefers direct svelte-check executable, falls back to bunx/npx.

        Returns:
            Command arguments for svelte-check.
        """
        # Prefer direct executable if available
        if shutil.which("svelte-check"):
            return ["svelte-check"]
        # Try bunx (bun)
        if shutil.which("bunx"):
            return ["bunx", "svelte-check"]
        # Try npx (npm)
        if shutil.which("npx"):
            return ["npx", "svelte-check"]
        # Last resort
        return ["svelte-check"]

    def _find_svelte_config(self, cwd: Path) -> Path | None:
        """Find svelte config file in the working directory.

        Args:
            cwd: Working directory to search for config.

        Returns:
            Path to svelte config if found, None otherwise.
        """
        config_names = ["svelte.config.js", "svelte.config.ts", "svelte.config.mjs"]
        for config_name in config_names:
            config_path = cwd / config_name
            if config_path.exists():
                return config_path
        return None

    def _build_command(
        self,
        options: dict[str, object] | None = None,
    ) -> list[str]:
        """Build the svelte-check invocation command.

        Args:
            options: Options dict to use for flags. Defaults to self.options.

        Returns:
            A list of command arguments ready to be executed.
        """
        if options is None:
            options = self.options

        cmd: list[str] = self._get_svelte_check_command()

        # Use machine-verbose output for parseable format
        cmd.extend(["--output", "machine-verbose"])

        # Threshold option
        threshold = options.get("threshold", "error")
        if threshold:
            cmd.extend(["--threshold", str(threshold)])

        # Tsconfig option
        tsconfig = options.get("tsconfig")
        if tsconfig:
            cmd.extend(["--tsconfig", str(tsconfig)])

        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return svelte-check documentation URL.

        Args:
            code: Error code (e.g., "ts-2322", "css-unused-selector").

        Returns:
            URL to the svelte-check documentation, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.SVELTE_CHECK

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with svelte-check.

        Svelte-check runs on the entire project and uses the project's
        svelte.config and tsconfig.json for configuration.

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
            no_files_message="No Svelte files to check.",
        )

        if ctx.should_skip and ctx.early_result is not None:
            return ctx.early_result

        # Safety check: if should_skip but no early_result, create one
        if ctx.should_skip:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No Svelte files to check.",
                issues_count=0,
            )

        logger.debug("[svelte-check] Discovered {} Svelte file(s)", len(ctx.files))

        cwd_path = Path(ctx.cwd) if ctx.cwd else Path.cwd()

        # Warn if no svelte config found, but still proceed with defaults
        svelte_config = self._find_svelte_config(cwd_path)
        if not svelte_config:
            logger.warning(
                "[svelte-check] No svelte.config.* found — proceeding with defaults",
            )

        # Check if dependencies need installing
        from lintro.utils.node_deps import install_node_deps, should_install_deps

        try:
            needs_install = should_install_deps(cwd_path)
        except PermissionError as e:
            logger.warning("[svelte-check] {}", e)
            return ToolResult(
                name=self.definition.name,
                success=True,
                output=f"Skipping svelte-check: {e}",
                issues_count=0,
                skipped=True,
                skip_reason="directory not writable",
            )

        if needs_install:
            auto_install = merged_options.get("auto_install", False)
            if auto_install:
                logger.info("[svelte-check] Auto-installing Node.js dependencies...")
                install_ok, install_output = install_node_deps(cwd_path)
                if install_ok:
                    logger.info(
                        "[svelte-check] Dependencies installed successfully",
                    )
                else:
                    logger.warning(
                        "[svelte-check] Auto-install failed, skipping: {}",
                        install_output,
                    )
                    return ToolResult(
                        name=self.definition.name,
                        success=True,
                        output=(
                            f"Skipping svelte-check: auto-install failed.\n"
                            f"{install_output}"
                        ),
                        issues_count=0,
                        skipped=True,
                        skip_reason="auto-install failed",
                    )
            else:
                return ToolResult(
                    name=self.definition.name,
                    output=(
                        "node_modules not found. "
                        "Use --auto-install to install dependencies."
                    ),
                    issues_count=0,
                    skipped=True,
                    skip_reason="node_modules not found",
                )

        # Build command
        cmd = self._build_command(options=merged_options)
        logger.debug("[svelte-check] Running with cwd={} and cmd={}", ctx.cwd, cmd)

        try:
            success, output = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=ctx.timeout,
                cmd=cmd,
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=timeout_result.issues_count,
                issues=timeout_result.issues,
            )
        except FileNotFoundError as e:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"svelte-check not found: {e}\n\n"
                "Please ensure svelte-check is installed:\n"
                "  - Run 'bun add -D svelte-check' or 'npm install -D svelte-check'\n"
                "  - Or install globally: 'bun add -g svelte-check'",
                issues_count=0,
            )
        except OSError as e:
            logger.error("[svelte-check] Failed to run svelte-check: {}", e)
            return ToolResult(
                name=self.definition.name,
                success=False,
                output="svelte-check execution failed: " + str(e),
                issues_count=0,
            )

        # Parse output
        all_issues = parse_svelte_check_output(output=output or "")
        issues_count = len(all_issues)

        # Normalize output for fallback analysis
        normalized_output = strip_ansi_codes(output) if output else ""

        # Handle dependency errors
        if not success and issues_count == 0 and normalized_output:
            if (
                "Cannot find module" in normalized_output
                or "Cannot find type definition" in normalized_output
            ):
                helpful_output = (
                    f"svelte-check configuration error:\n{normalized_output}\n\n"
                    "This usually means dependencies aren't installed.\n"
                    "Suggestions:\n"
                    "  - Run 'bun install' or 'npm install' in your project\n"
                    "  - Use '--auto-install' flag to auto-install dependencies"
                )
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=helpful_output,
                    issues_count=0,
                )

            # Generic failure
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=normalized_output or "svelte-check execution failed.",
                issues_count=0,
            )

        if not success and issues_count == 0:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output="svelte-check execution failed.",
                issues_count=0,
            )

        return ToolResult(
            name=self.definition.name,
            success=success and issues_count == 0,
            output=None,
            issues_count=issues_count,
            issues=all_issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> NoReturn:
        """Svelte-check does not support auto-fixing.

        Args:
            paths: Paths or files passed for completeness.
            options: Runtime options (unused).

        Raises:
            NotImplementedError: Always, because svelte-check cannot fix issues.
        """
        raise NotImplementedError(
            "svelte-check cannot automatically fix issues. Type errors and "
            "linting issues require manual code changes.",
        )
