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
    unless ``allowJs``/``checkJs`` is set or a file starts with ``// @ts-check``.
    Lintro skips JS-only invocations early only when the *effective* file set is
    JavaScript-only, no discovered tsconfig enables ``checkJs``, no input has
    ``@ts-check``, ``extends`` targets are fully resolved, and the caller did
    not request native project selection (``use_project_files`` / ``project``).
    When ``checkJs``/``allowJs`` is off, discovered ``.js``/``.jsx`` files are
    dropped from the tsc file list so mixed TS+JS trees do not hit TS6504.

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
from lintro.utils.tsconfig import discover_tsconfigs, resolve_extends_chain
from lintro.utils.tsconfig_info import TsconfigInfo

# Constants for Tsc configuration
TSC_DEFAULT_TIMEOUT: int = 60
TSC_DEFAULT_PRIORITY: int = 82  # Same as mypy (type checkers)
# One ordered table drives discovery globs and suffix frozensets.
_TS_EXTENSIONS_ORDERED: tuple[str, ...] = (".ts", ".tsx", ".mts", ".cts")
_JS_EXTENSIONS_ORDERED: tuple[str, ...] = (".js", ".mjs", ".cjs", ".jsx")
_TS_EXTENSIONS: frozenset[str] = frozenset(_TS_EXTENSIONS_ORDERED)
_JS_EXTENSIONS: frozenset[str] = frozenset(_JS_EXTENSIONS_ORDERED)
TSC_FILE_PATTERNS: list[str] = [
    f"*{ext}" for ext in (*_TS_EXTENSIONS_ORDERED, *_JS_EXTENSIONS_ORDERED)
]
_SKIP_CHECKJS_REASON: str = "checkJs not enabled for JavaScript-only check"
_TS_CHECK_HEADER_BYTES: int = 8192

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


@dataclass(frozen=True)
class _JsGating:
    """Aggregated JavaScript type-check flags from relevant tsconfigs."""

    check_js: bool
    allow_js: bool
    unresolved_extends: bool


def _is_native_project_mode(merged_options: dict[str, object]) -> bool:
    """Return whether the caller requested native tsconfig file selection.

    Args:
        merged_options: Merged runtime options.

    Returns:
        ``True`` when ``use_project_files`` or ``project`` is set.
    """
    if merged_options.get("use_project_files"):
        return True
    project = merged_options.get("project")
    return isinstance(project, str) and bool(project)


def _is_js_only(*, files: list[str]) -> bool:
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


def _js_files_with_ts_check(*, files: list[str]) -> set[str]:
    """Return JS paths whose header enables native tsc via ``@ts-check``.

    Args:
        files: Absolute file paths discovered for the check.

    Returns:
        Absolute paths of JavaScript files with a header ``@ts-check``.
    """
    checked: set[str] = set()
    for filepath in files:
        if Path(filepath).suffix.lower() not in _JS_EXTENSIONS:
            continue
        if _js_file_has_ts_check(filepath=filepath):
            checked.add(filepath)
    return checked


def _js_file_has_ts_check(*, filepath: str) -> bool:
    """Return whether a JS file header contains ``// @ts-check``.

    Mirrors native tsc: the pragma is honoured in the comment/shebang
    header before the first non-comment statement. ``@ts-nocheck`` wins
    if both appear in that header.

    Args:
        filepath: Absolute path of a JavaScript file.

    Returns:
        ``True`` when the header enables per-file JS type checking.
    """
    try:
        with Path(filepath).open(encoding="utf-8", errors="replace") as handle:
            header = handle.read(_TS_CHECK_HEADER_BYTES)
    except OSError:
        return False

    saw_check = False
    in_block = False
    for raw_line in header.splitlines():
        stripped = raw_line.strip()
        if in_block:
            if "@ts-nocheck" in stripped:
                return False
            if "@ts-check" in stripped:
                saw_check = True
            if "*/" in stripped:
                in_block = False
            continue
        if not stripped or stripped.startswith("#!"):
            continue
        if stripped.startswith("/*"):
            if "@ts-nocheck" in stripped:
                return False
            if "@ts-check" in stripped:
                saw_check = True
            if "*/" not in stripped:
                in_block = True
            continue
        if stripped.startswith("//"):
            if "@ts-nocheck" in stripped:
                return False
            if "@ts-check" in stripped:
                saw_check = True
            continue
        break
    return saw_check


def _drop_uncheckable_js(
    *,
    ctx: ExecutionContext,
    keep_abs_paths: set[str],
) -> None:
    """Drop JavaScript files tsc would reject without allowJs/checkJs.

    TypeScript files and JS files with ``@ts-check`` (in *keep_abs_paths*)
    are retained. Mutates *ctx* in place.

    Args:
        ctx: Prepared execution context with discovered files.
        keep_abs_paths: Absolute JS paths that must still be passed to tsc.
    """
    kept_files: list[str] = []
    kept_rel: list[str] = []
    for abs_path, rel_path in zip(ctx.files, ctx.rel_files, strict=True):
        suffix = Path(abs_path).suffix.lower()
        if suffix in _JS_EXTENSIONS and abs_path not in keep_abs_paths:
            continue
        kept_files.append(abs_path)
        kept_rel.append(rel_path)
    ctx.files = kept_files
    ctx.rel_files = kept_rel


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
        """Skip JS-only checks that native tsc would not type-check.

        Native tsc ignores JavaScript unless ``checkJs`` is set or a file
        starts with ``// @ts-check``. Skipping early avoids spurious tsc
        runs (and node_modules install prompts) for plain JS trees that
        happen to match the expanded file patterns.

        This hook does **not** skip when:

        * ``use_project_files`` or ``project`` is set (native tsconfig
          file selection may still type-check TypeScript).
        * An ``extends`` target is unresolved (fail closed so auto-install
          can populate an npm parent that enables ``checkJs``).
        * Any discovered JS file has a header ``@ts-check`` pragma.

        When ``checkJs``/``allowJs`` is off, uncheckable JavaScript is
        dropped from ``ctx.files`` / ``ctx.rel_files`` so mixed TS+JS
        trees do not pass ``.js`` into a temp tsconfig (TS6504).

        Args:
            ctx: Prepared execution context with discovered files.
            paths: Original input paths passed to ``check``.
            cwd_path: Prepared execution working directory.
            merged_options: Merged runtime options.

        Returns:
            A skipped ToolResult when the *effective* file set is JS-only
            and nothing would be type-checked; otherwise ``None``.
        """
        if _is_native_project_mode(merged_options):
            return None

        gating = self._js_gating(
            cwd_path=cwd_path,
            paths=paths,
            merged_options=merged_options,
        )
        if gating.unresolved_extends:
            logger.debug(
                "[tsc] Not skipping: unresolved tsconfig extends (fail closed)",
            )
            return None

        ts_check_js = (
            set()
            if gating.check_js
            else _js_files_with_ts_check(files=ctx.files)
        )
        if (
            _is_js_only(files=ctx.files)
            and not gating.check_js
            and not ts_check_js
        ):
            logger.debug(
                "[tsc] Skipping JS-only check: no tsconfig enables checkJs",
            )
            return self._skipped_checkjs_result()

        if not gating.check_js and not gating.allow_js:
            _drop_uncheckable_js(ctx=ctx, keep_abs_paths=ts_check_js)
            if not ctx.files:
                return self._skipped_checkjs_result()

        return None

    def _skipped_checkjs_result(self) -> ToolResult:
        """Return the standard JS-only / no-checkJs skip result.

        Returns:
            A successful skipped ToolResult.
        """
        return ToolResult(
            name=self.definition.name,
            success=True,
            output=(
                "Skipping tsc: JavaScript-only inputs and no tsconfig enables checkJs."
            ),
            issues_count=0,
            skipped=True,
            skip_reason=_SKIP_CHECKJS_REASON,
        )

    def _js_gating(
        self,
        cwd_path: Path,
        paths: list[str],
        merged_options: dict[str, object],
    ) -> _JsGating:
        """Aggregate checkJs/allowJs/unresolved-extends from relevant tsconfigs.

        Honours an explicit ``project`` option when set; otherwise discovers
        tsconfigs from the same root used by the normal check path. Discovery
        already returns :func:`~lintro.utils.tsconfig.resolve_extends_chain`
        results, so compiler options are not walked a second time.

        Args:
            cwd_path: Prepared execution working directory.
            paths: Original input paths passed to ``check``.
            merged_options: Merged runtime options.

        Returns:
            Aggregated JavaScript gating flags.
        """
        infos = self._relevant_tsconfigs(
            cwd_path=cwd_path,
            paths=paths,
            merged_options=merged_options,
        )
        check_js = False
        allow_js = False
        unresolved_extends = False
        for info in infos:
            opts = info.compiler_options
            if opts.get("checkJs") is True:
                check_js = True
            if opts.get("allowJs") is True or opts.get("checkJs") is True:
                allow_js = True
            if info.unresolved_extends:
                unresolved_extends = True
        return _JsGating(
            check_js=check_js,
            allow_js=allow_js,
            unresolved_extends=unresolved_extends,
        )

    def _relevant_tsconfigs(
        self,
        cwd_path: Path,
        paths: list[str],
        merged_options: dict[str, object],
    ) -> list[TsconfigInfo]:
        """Return tsconfigs that govern this check's JavaScript gating.

        Args:
            cwd_path: Prepared execution working directory.
            paths: Original input paths passed to ``check``.
            merged_options: Merged runtime options.

        Returns:
            Resolved tsconfig infos (possibly empty).
        """
        explicit_project = merged_options.get("project")
        if isinstance(explicit_project, str) and explicit_project:
            project_path = Path(explicit_project)
            if not project_path.is_absolute():
                project_path = (cwd_path / project_path).resolve()
            else:
                project_path = project_path.resolve()
            if project_path.exists():
                return [resolve_extends_chain(project_path)]
            return []

        discovery_root = self._compute_discovery_root(
            cwd_path=cwd_path,
            paths=paths,
        )
        tsconfigs = discover_tsconfigs(
            root=discovery_root,
            exclude_patterns=self.exclude_patterns,
        )
        if tsconfigs:
            return tsconfigs

        nearest = self._find_tsconfig(cwd_path)
        if nearest is not None:
            return [resolve_extends_chain(nearest)]
        return []

    def _get_tsc_command(self, cwd: Path | None = None) -> list[str]:
        """Get the command to run tsc.

        Resolves the project-local ``typescript`` install first, then ``PATH``,
        then a version-pinned ``bunx``/``npx`` invocation (#1811).

        Args:
            cwd: Directory tsc will run in, when known.

        Returns:
            Command arguments for tsc.
        """
        return self._resolve_binary_command("tsc", cwd=cwd)

    def _command_prefix(self, cwd: Path | None = None) -> list[str]:
        """Return the tsc command prefix.

        Args:
            cwd: Directory tsc will run in, when known.

        Returns:
            Command argument list for tsc.
        """
        return self._get_tsc_command(cwd=cwd)

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
