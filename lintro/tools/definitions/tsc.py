"""Tsc (TypeScript Compiler) tool definition.

Tsc is the TypeScript compiler which performs static type checking on
TypeScript files. It helps catch type-related bugs before runtime by
analyzing type annotations and inferences.

File Targeting Behavior:
    By default, lintro respects your file selection even when tsconfig.json exists.
    This is achieved by creating a temporary tsconfig that extends your project's
    config but overrides the `include` pattern to target only the specified files.

    To use native tsconfig.json file selection instead, set `use_project_files=True`.

Example:
    # Check only specific files (default behavior)
    lintro check src/utils.ts --tools tsc

    # Check all files defined in tsconfig.json
    lintro check . --tools tsc --tool-options "tsc:use_project_files=True"

Most of the orchestration (command construction, tsconfig discovery, single- and
multi-project execution, output shaping) lives in the shared
:class:`lintro.tools.definitions._ts_checker_base.TypeScriptCheckerPlugin` base.
This module supplies the tsc-specific deltas: the binary command, TypeScript file
extensions, tsc output parsing, framework detection, and error-message copy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.parsers.tsc.tsc_parser import (
    categorize_tsc_issues,
    extract_missing_modules,
    parse_tsc_output,
)
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.definitions._ts_checker_base import TypeScriptCheckerPlugin

# Constants for Tsc configuration
TSC_DEFAULT_TIMEOUT: int = 60
TSC_DEFAULT_PRIORITY: int = 82  # Same as mypy (type checkers)
TSC_FILE_PATTERNS: list[str] = ["*.ts", "*.tsx", "*.mts", "*.cts"]

# Framework config files that indicate tsc should defer to framework-specific checker
# Note: vite.config.ts is NOT included for Vue because it's used by many
# non-Vue projects (e.g., React, vanilla TS, Svelte without svelte.config)
FRAMEWORK_CONFIGS: dict[str, tuple[str, list[str]]] = {
    "Astro": (
        "astro-check",
        ["astro.config.mjs", "astro.config.ts", "astro.config.js"],
    ),
    "Vue": (
        "vue-tsc",
        ["vue.config.js", "vue.config.ts"],
    ),
    "Svelte": (
        "svelte-check",
        ["svelte.config.js", "svelte.config.ts"],
    ),
}


@register_tool
@dataclass
class TscPlugin(TypeScriptCheckerPlugin):
    """TypeScript Compiler (tsc) type checking plugin.

    This plugin integrates the TypeScript compiler with Lintro for static
    type checking of TypeScript files.
    """

    _tool_label: ClassVar[str] = "tsc"
    _file_kind: ClassVar[str] = "TypeScript"
    _no_files_message: ClassVar[str] = "No TypeScript files to check."
    _temp_config_prefix: ClassVar[str] = ".lintro-tsc-"
    _fix_error_message: ClassVar[str] = (
        "Tsc cannot automatically fix issues. Type errors require "
        "manual code changes."
    )
    _tsconfig_candidates: ClassVar[tuple[str, ...]] = ("tsconfig.json",)

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="tsc",
            description="TypeScript compiler for static type checking",
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.TYPE_CHECKER,
            file_patterns=TSC_FILE_PATTERNS,
            priority=TSC_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["tsconfig.json"],
            version_command=["tsc", "--version"],
            min_version=get_min_version(ToolName.TSC),
            default_options={
                "timeout": TSC_DEFAULT_TIMEOUT,
                "project": None,
                "strict": None,
                "skip_lib_check": True,
                "use_project_files": False,
            },
            default_timeout=TSC_DEFAULT_TIMEOUT,
        )

    def _get_tsc_command(self) -> list[str]:
        """Get the command to run tsc.

        Prefers direct tsc executable, falls back to bunx/npx.

        Returns:
            Command arguments for tsc.
        """
        return self._resolve_binary_command("tsc")

    def _command_prefix(self) -> list[str]:
        """Return the tsc command prefix.

        Returns:
            Command argument list for tsc.
        """
        return self._get_tsc_command()

    def _detect_framework_project(self, cwd: Path) -> tuple[str, str] | None:
        """Detect if the project uses a framework with its own type checker.

        Frameworks like Astro, Vue, and Svelte have their own type checkers
        that handle framework-specific syntax (e.g., .astro, .vue, .svelte files).
        When these frameworks are detected, tsc should skip and defer to the
        framework-specific tool.

        Args:
            cwd: Working directory to search for framework config files.

        Returns:
            Tuple of (framework_name, recommended_tool) if detected, None otherwise.
        """
        for framework_name, (tool_name, config_files) in FRAMEWORK_CONFIGS.items():
            for config_file in config_files:
                if (cwd / config_file).exists():
                    logger.debug(
                        "[tsc] Detected {} project (found {})",
                        framework_name,
                        config_file,
                    )
                    return (framework_name, tool_name)
        return None

    def _compute_discovery_root(self, cwd_path: Path, paths: list[str]) -> Path:
        """Compute the tsconfig discovery root as the common ancestor of paths.

        Using the common ancestor of all input paths ensures tsconfigs in
        sibling packages are discovered when multiple paths are provided.

        Args:
            cwd_path: The prepared execution working directory.
            paths: The original input paths.

        Returns:
            Directory to scan for tsconfigs.
        """
        discovery_root = cwd_path
        if paths:
            resolved_dirs = []
            for p in paths:
                r = Path(p).resolve()
                resolved_dirs.append(str(r if r.is_dir() else r.parent))
            if resolved_dirs:
                common = Path(os.path.commonpath(resolved_dirs))
                if common.exists():
                    discovery_root = common
        return discovery_root

    def _parse_output(self, output: str) -> list[Any]:
        """Parse raw tsc output into structured issues.

        Args:
            output: Raw stdout/stderr text from tsc.

        Returns:
            List of parsed tsc issue objects.
        """
        return parse_tsc_output(output=output)

    def _categorize_issues(
        self,
        issues: list[Any],
    ) -> tuple[list[Any], list[Any]]:
        """Split tsc issues into (type errors, dependency errors).

        Args:
            issues: Parsed tsc issue objects.

        Returns:
            A ``(type_errors, dependency_errors)`` tuple.
        """
        return categorize_tsc_issues(issues)

    def _extract_missing_modules(self, dependency_errors: list[Any]) -> list[str]:
        """Extract missing module names from tsc dependency errors.

        Args:
            dependency_errors: Dependency-related tsc issue objects.

        Returns:
            List of missing module names.
        """
        return extract_missing_modules(dependency_errors)

    def _not_found_output(self, error: FileNotFoundError) -> str:
        """Build guidance shown when the tsc binary is not found.

        Args:
            error: The FileNotFoundError raised while launching tsc.

        Returns:
            User-facing guidance text.
        """
        return (
            f"TypeScript compiler not found: {error}\n\n"
            "Please ensure tsc is installed:\n"
            "  - Run 'npm install -g typescript' or 'bun add -g typescript'\n"
            "  - Or install locally: 'npm install typescript'"
        )

    def _config_error_output(self, normalized_output: str) -> str:
        """Build guidance shown for a likely dependency/config error.

        Args:
            normalized_output: ANSI-stripped tsc output.

        Returns:
            User-facing guidance text.
        """
        return (
            f"TypeScript configuration error:\n{normalized_output}\n\n"
            "This usually means dependencies aren't installed.\n"
            "Suggestions:\n"
            "  - Run 'bun install' or 'npm install' in your project\n"
            "  - Use '--auto-install' flag to auto-install dependencies\n"
            "  - If using Docker, ensure node_modules is available\n"
            "  - Use --tool-options 'tsc:skip_lib_check=true' to skip "
            "type checking of declaration files"
        )
