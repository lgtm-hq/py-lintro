"""Tsc (TypeScript Compiler) tool definition.

Tsc is the TypeScript compiler which performs static type checking on
TypeScript files and, when a project's tsconfig enables ``checkJs``, on
JSDoc-typed JavaScript as well. It helps catch type-related bugs before
runtime by analyzing type annotations and inferences.

File Targeting Behavior:
    By default, lintro respects your file selection even when tsconfig.json exists.
    This is achieved by creating a temporary tsconfig that extends your project's
    config but overrides the `include` pattern to target only the specified files.

    To use native tsconfig.json file selection instead, set `use_project_files=True`.

    JavaScript files (``*.js`` / ``*.mjs`` / ``*.cjs`` / ``*.jsx``) are included in
    discovery so JSDoc-typed projects activate the plugin. Native tsc ignores JS
    unless ``allowJs``/``checkJs`` is set; lintro additionally skips JS-only
    invocations early when no discovered tsconfig enables ``checkJs``.

Example:
    # Check only specific files (default behavior)
    lintro check src/utils.ts --tools tsc

    # Check all files defined in tsconfig.json
    lintro check . --tools tsc --tool-options "tsc:use_project_files=True"

Most of the orchestration (command construction, tsconfig discovery, single- and
multi-project execution, output shaping) lives in the shared
:class:`lintro.tools.definitions._ts_checker_base.TypeScriptCheckerPlugin` base.
This module supplies the tsc-specific deltas: the binary command, TypeScript and
JavaScript file extensions, tsc output parsing, framework detection, JS-only
``checkJs`` gating, and error-message copy.
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
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.tsc.tsc_parser import (
    categorize_tsc_issues,
    extract_missing_modules,
    parse_tsc_output,
)
from lintro.plugins.base import ExecutionContext
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.definitions._ts_checker_base import TypeScriptCheckerPlugin
from lintro.utils.tsconfig import discover_tsconfigs, enables_check_js

# Constants for Tsc configuration
TSC_DEFAULT_TIMEOUT: int = 60
TSC_DEFAULT_PRIORITY: int = 82  # Same as mypy (type checkers)
TSC_FILE_PATTERNS: list[str] = [
    "*.ts",
    "*.tsx",
    "*.mts",
    "*.cts",
    "*.js",
    "*.mjs",
    "*.cjs",
    "*.jsx",
]
_JS_EXTENSIONS: frozenset[str] = frozenset({".js", ".mjs", ".cjs", ".jsx"})
_TS_EXTENSIONS: frozenset[str] = frozenset({".ts", ".tsx", ".mts", ".cts"})

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
    type checking of TypeScript files and JSDoc-typed JavaScript when
    ``checkJs`` is enabled.
    """

    _tool_label: ClassVar[str] = "tsc"
    _file_kind: ClassVar[str] = "TypeScript/JavaScript"
    _no_files_message: ClassVar[str] = "No TypeScript or JavaScript files to check."
    _temp_config_prefix: ClassVar[str] = ".lintro-tsc-"
    _fix_error_message: ClassVar[str] = (
        "Tsc cannot automatically fix issues. Type errors require manual code changes."
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
            description=(
                "TypeScript compiler for static type checking "
                "(including JSDoc JavaScript when checkJs is enabled)"
            ),
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

    def _pre_run_skip(
        self,
        ctx: ExecutionContext,
        paths: list[str],
        cwd_path: Path,
        merged_options: dict[str, object],
    ) -> ToolResult | None:
        """Skip JS-only checks when no discovered tsconfig enables checkJs.

        Native tsc ignores JavaScript unless ``checkJs`` is set. Skipping
        early avoids spurious tsc runs (and node_modules install prompts)
        for plain JS trees that happen to match the expanded file patterns.

        Args:
            ctx: Prepared execution context with discovered files.
            paths: Original input paths passed to ``check``.
            cwd_path: Prepared execution working directory.
            merged_options: Merged runtime options.

        Returns:
            A skipped ToolResult when the invocation is JS-only and no
            relevant tsconfig enables ``checkJs``; otherwise ``None``.
        """
        if not self._is_js_only(ctx.files):
            return None
        if self._any_check_js_enabled(cwd_path, paths, merged_options):
            return None

        logger.debug(
            "[tsc] Skipping JS-only check: no tsconfig enables checkJs",
        )
        return ToolResult(
            name=self.definition.name,
            success=True,
            output=(
                "Skipping tsc: JavaScript-only inputs and no tsconfig enables checkJs."
            ),
            issues_count=0,
            skipped=True,
            skip_reason="checkJs not enabled for JavaScript-only check",
        )

    @staticmethod
    def _is_js_only(files: list[str]) -> bool:
        """Return whether all discovered files are JavaScript (no TypeScript).

        Args:
            files: Absolute file paths discovered for the check.

        Returns:
            ``True`` when every file has a JavaScript extension and none
            have a TypeScript extension.
        """
        if not files:
            return False
        has_js = False
        for filepath in files:
            suffix = Path(filepath).suffix.lower()
            if suffix in _TS_EXTENSIONS:
                return False
            if suffix in _JS_EXTENSIONS:
                has_js = True
        return has_js

    def _any_check_js_enabled(
        self,
        cwd_path: Path,
        paths: list[str],
        merged_options: dict[str, object],
    ) -> bool:
        """Return whether any relevant tsconfig enables ``checkJs``.

        Honours an explicit ``project`` option when set; otherwise discovers
        tsconfigs from the same root used by the normal check path.

        Args:
            cwd_path: Prepared execution working directory.
            paths: Original input paths passed to ``check``.
            merged_options: Merged runtime options.

        Returns:
            ``True`` if at least one relevant tsconfig enables ``checkJs``.
        """
        explicit_project = merged_options.get("project")
        if isinstance(explicit_project, str) and explicit_project:
            project_path = Path(explicit_project)
            if not project_path.is_absolute():
                project_path = (cwd_path / project_path).resolve()
            else:
                project_path = project_path.resolve()
            return project_path.exists() and enables_check_js(project_path)

        discovery_root = self._compute_discovery_root(cwd_path, paths)
        tsconfigs = discover_tsconfigs(discovery_root, self.exclude_patterns)
        if any(enables_check_js(info.path) for info in tsconfigs):
            return True

        # Fall back to the nearest candidate tsconfig when discovery finds
        # nothing (e.g. unusual working-directory layouts).
        nearest = self._find_tsconfig(cwd_path)
        return nearest is not None and enables_check_js(nearest)

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
