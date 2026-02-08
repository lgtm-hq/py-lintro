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
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - used safely with shell disabled
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.tsc.tsc_parser import (
    categorize_tsc_issues,
    extract_missing_modules,
    parse_tsc_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.timeout_utils import create_timeout_result
from lintro.utils.jsonc import extract_type_roots, load_jsonc

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
class TscPlugin(BaseToolPlugin):
    """TypeScript Compiler (tsc) type checking plugin.

    This plugin integrates the TypeScript compiler with Lintro for static
    type checking of TypeScript files.
    """

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

    def set_options(  # type: ignore[override]
        self,
        project: str | None = None,
        strict: bool | None = None,
        skip_lib_check: bool | None = None,
        use_project_files: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set tsc-specific options.

        Args:
            project: Path to tsconfig.json file.
            strict: Enable strict type checking mode.
            skip_lib_check: Skip type checking of declaration files (default: True).
            use_project_files: When True, use tsconfig.json's include/files patterns
                instead of lintro's file targeting. Default is False, meaning lintro
                respects your file selection even when tsconfig.json exists.
            **kwargs: Other tool options.

        Raises:
            ValueError: If any provided option is of an unexpected type.
        """
        if project is not None and not isinstance(project, str):
            raise ValueError("project must be a string path")
        if strict is not None and not isinstance(strict, bool):
            raise ValueError("strict must be a boolean")
        if skip_lib_check is not None and not isinstance(skip_lib_check, bool):
            raise ValueError("skip_lib_check must be a boolean")
        if use_project_files is not None and not isinstance(use_project_files, bool):
            raise ValueError("use_project_files must be a boolean")

        options: dict[str, object] = {
            "project": project,
            "strict": strict,
            "skip_lib_check": skip_lib_check,
            "use_project_files": use_project_files,
        }
        options = {k: v for k, v in options.items() if v is not None}
        super().set_options(**options, **kwargs)

    def _get_tsc_command(self) -> list[str]:
        """Get the command to run tsc.

        Prefers direct tsc executable, falls back to bunx/npx.

        Returns:
            Command arguments for tsc.
        """
        # Prefer direct executable if available
        if shutil.which("tsc"):
            return ["tsc"]
        # Try bunx (bun) - note: bunx tsc works if typescript is installed
        if shutil.which("bunx"):
            return ["bunx", "tsc"]
        # Try npx (npm)
        if shutil.which("npx"):
            return ["npx", "tsc"]
        # Last resort - hope tsc is in PATH
        return ["tsc"]

    def _find_tsconfig(self, cwd: Path) -> Path | None:
        """Find tsconfig.json in the working directory or via project option.

        Args:
            cwd: Working directory to search for tsconfig.json.

        Returns:
            Path to tsconfig.json if found, None otherwise.
        """
        # Check explicit project option first
        project_opt = self.options.get("project")
        if project_opt and isinstance(project_opt, str):
            project_path = Path(project_opt)
            if project_path.is_absolute():
                return project_path if project_path.exists() else None
            resolved = cwd / project_path
            return resolved if resolved.exists() else None

        # Check for tsconfig.json in cwd
        tsconfig = cwd / "tsconfig.json"
        return tsconfig if tsconfig.exists() else None

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

    def _create_temp_tsconfig(
        self,
        base_tsconfig: Path,
        files: list[str],
        cwd: Path,
    ) -> Path:
        """Create a temporary tsconfig.json that extends the base config.

        This allows lintro to respect user file selection while preserving
        all compiler options from the project's tsconfig.json.

        Args:
            base_tsconfig: Path to the original tsconfig.json to extend.
            files: List of file paths to include (relative to cwd).
            cwd: Working directory for resolving paths.

        Returns:
            Path to the temporary tsconfig.json file.

        Raises:
            OSError: If the temporary file cannot be created or written.
        """
        abs_base = base_tsconfig.resolve()

        # Convert relative file paths to absolute paths since the temp tsconfig
        # may be in a different directory than cwd
        abs_files = [str((cwd / f).resolve()) for f in files]

        compiler_options: dict[str, Any] = {
            # Ensure noEmit is set (type checking only)
            "noEmit": True,
        }

        # Read typeRoots from the base tsconfig so they are preserved in the
        # temp config.  TypeScript resolves typeRoots relative to the config
        # file, so we resolve them to absolute paths here because the temp
        # config lives in a different directory.
        try:
            base_content = load_jsonc(abs_base.read_text(encoding="utf-8"))
            resolved_roots = extract_type_roots(base_content, abs_base.parent)
            if resolved_roots is not None:
                compiler_options["typeRoots"] = resolved_roots
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("[tsc] Could not read typeRoots from {}: {}", abs_base, exc)

        temp_config = {
            "extends": str(abs_base),
            "include": abs_files,
            "exclude": [],
            "compilerOptions": compiler_options,
        }

        # Create temp file next to the base tsconfig so TypeScript can resolve
        # types/typeRoots by walking up from the temp file to node_modules.
        # Falls back to system temp dir with explicit typeRoots for read-only
        # filesystems (e.g. Docker volume mounts).
        try:
            fd, temp_path = tempfile.mkstemp(
                suffix=".json",
                prefix=".lintro-tsc-",
                dir=abs_base.parent,
            )
        except OSError:
            fd, temp_path = tempfile.mkstemp(
                suffix=".json",
                prefix="lintro-tsc-",
            )
            # Preserve existing typeRoots from the base tsconfig and add
            # the default node_modules/@types path so TypeScript can still
            # resolve type packages from the system temp dir.
            existing_type_roots: list[str] = []
            type_roots_present = False
            try:
                base_content = load_jsonc(
                    base_tsconfig.read_text(encoding="utf-8"),
                )
                extracted = extract_type_roots(base_content, abs_base.parent)
                if extracted is not None:
                    existing_type_roots = extracted
                    type_roots_present = True
            except (json.JSONDecodeError, OSError):
                pass
            default_root = str(cwd / "node_modules" / "@types")
            if not type_roots_present and default_root not in existing_type_roots:
                existing_type_roots.append(default_root)
            compiler_options["typeRoots"] = existing_type_roots

        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(temp_config, f, indent=2)
        except OSError:
            # Clean up on failure
            Path(temp_path).unlink(missing_ok=True)
            raise

        logger.debug(
            "[tsc] Created temp tsconfig at {} extending {} with {} files",
            temp_path,
            abs_base,
            len(files),
        )
        return Path(temp_path)

    def _build_command(
        self,
        files: list[str],
        project_path: str | Path | None = None,
        options: dict[str, object] | None = None,
    ) -> list[str]:
        """Build the tsc invocation command.

        Args:
            files: Relative file paths (used only when no project config).
            project_path: Path to tsconfig.json to use (temp or user-specified).
            options: Options dict to use for flags. Defaults to self.options.

        Returns:
            A list of command arguments ready to be executed.
        """
        if options is None:
            options = self.options

        cmd: list[str] = self._get_tsc_command()

        # Core flags for linting (no output, machine-readable format)
        cmd.extend(["--noEmit", "--pretty", "false"])

        # Project flag (uses tsconfig.json - either temp, explicit, or auto-discovered)
        if project_path:
            cmd.extend(["--project", str(project_path)])

        # Strict mode override (--strict is off by default, no flag needed for False)
        if options.get("strict") is True:
            cmd.append("--strict")

        # Skip lib check (faster, avoids issues with node_modules types)
        if options.get("skip_lib_check", True):
            cmd.append("--skipLibCheck")

        # Only pass files directly if no project config is being used
        if not project_path and files:
            cmd.extend(files)

        return cmd

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with tsc.

        By default, lintro respects your file selection even when tsconfig.json exists.
        This is achieved by creating a temporary tsconfig that extends your project's
        config but targets only the specified files.

        To use native tsconfig.json file selection instead, set use_project_files=True.

        Note: For projects using Astro, Vue, or Svelte, tsc will skip and recommend
        using the framework-specific type checker (astro-check, vue-tsc, svelte-check)
        which handles framework-specific syntax.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Determine working directory for framework detection
        # Verify the path exists before using it (paths[0] could be a glob or missing)
        cwd_for_detection = Path.cwd()
        if paths:
            candidate = Path(paths[0]).resolve()
            if candidate.exists():
                if candidate.is_file():
                    cwd_for_detection = candidate.parent
                else:
                    cwd_for_detection = candidate
            elif candidate.parent.exists():
                cwd_for_detection = candidate.parent

        # Check for framework-specific projects that have their own type checkers
        framework_info = self._detect_framework_project(cwd_for_detection)
        if framework_info:
            framework_name, recommended_tool = framework_info
            return ToolResult(
                name=self.definition.name,
                success=True,
                output=(
                    f"SKIPPED: {framework_name} project detected.\n"
                    f"{framework_name} has its own type checker that handles "
                    f"framework-specific syntax.\n"
                    f"Use '{recommended_tool}' instead: "
                    f"lintro check . --tools {recommended_tool}"
                ),
                issues_count=0,
                skipped=True,
                skip_reason=f"deferred to {recommended_tool}",
            )

        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No TypeScript files to check.",
        )

        if ctx.should_skip and ctx.early_result is not None:
            return ctx.early_result

        # Safety check: if should_skip but no early_result, create one
        if ctx.should_skip:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No TypeScript files to check.",
                issues_count=0,
            )

        logger.debug("[tsc] Discovered {} TypeScript file(s)", len(ctx.files))

        # Determine project configuration strategy
        cwd_path = Path(ctx.cwd) if ctx.cwd else Path.cwd()

        # Check if dependencies need installing
        from lintro.utils.node_deps import install_node_deps, should_install_deps

        if should_install_deps(cwd_path):
            auto_install = merged_options.get("auto_install", False)
            if auto_install:
                logger.info("[tsc] Auto-installing Node.js dependencies...")
                install_ok, install_output = install_node_deps(cwd_path)
                if install_ok:
                    logger.info("[tsc] Dependencies installed successfully")
                else:
                    logger.warning(
                        "[tsc] Auto-install failed, skipping: {}",
                        install_output,
                    )
                    return ToolResult(
                        name=self.definition.name,
                        success=True,
                        output=(
                            f"Skipping tsc: auto-install failed.\n" f"{install_output}"
                        ),
                        issues_count=0,
                        skipped=True,
                        skip_reason="auto-install failed",
                    )
            else:
                return ToolResult(
                    name=self.definition.name,
                    success=True,
                    output=(
                        "node_modules not found. "
                        "Use --auto-install to install dependencies."
                    ),
                    issues_count=0,
                    skipped=True,
                    skip_reason="node_modules not found",
                )

        use_project_files = merged_options.get("use_project_files", False)
        explicit_project_opt = merged_options.get("project")
        explicit_project = str(explicit_project_opt) if explicit_project_opt else None
        temp_tsconfig: Path | None = None
        project_path: str | None = None

        try:
            # Find existing tsconfig.json
            base_tsconfig = self._find_tsconfig(cwd_path)

            if use_project_files or explicit_project:
                # Native mode: use tsconfig.json as-is for file selection
                # or explicit project path was provided
                project_path = explicit_project or (
                    str(base_tsconfig) if base_tsconfig else None
                )
                logger.debug(
                    "[tsc] Using native tsconfig file selection: {}",
                    project_path,
                )
            elif base_tsconfig:
                # Lintro mode: create temp tsconfig to respect file targeting
                # while preserving compiler options from the project's config
                temp_tsconfig = self._create_temp_tsconfig(
                    base_tsconfig=base_tsconfig,
                    files=ctx.rel_files,
                    cwd=cwd_path,
                )
                project_path = str(temp_tsconfig)
                logger.debug(
                    "[tsc] Using temp tsconfig for file targeting: {}",
                    project_path,
                )
            else:
                # No tsconfig.json found - pass files directly
                project_path = None
                logger.debug("[tsc] No tsconfig.json found, passing files directly")

            # Build command
            cmd = self._build_command(
                files=ctx.rel_files if not project_path else [],
                project_path=project_path,
                options=merged_options,
            )
            logger.debug("[tsc] Running with cwd={} and cmd={}", ctx.cwd, cmd)

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
                    output=f"TypeScript compiler not found: {e}\n\n"
                    "Please ensure tsc is installed:\n"
                    "  - Run 'npm install -g typescript' or 'bun add -g typescript'\n"
                    "  - Or install locally: 'npm install typescript'",
                    issues_count=0,
                )
            except OSError as e:
                logger.error("[tsc] Failed to run tsc: {}", e)
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output="tsc execution failed: " + str(e),
                    issues_count=0,
                )

            # Parse output (parser handles ANSI stripping internally)
            all_issues = parse_tsc_output(output=output or "")
            issues_count = len(all_issues)

            # Normalize output for fallback substring matching below
            normalized_output = strip_ansi_codes(output) if output else ""

            # Categorize issues into type errors vs dependency errors
            type_errors, dependency_errors = categorize_tsc_issues(all_issues)

            # If we have dependency errors, provide helpful guidance
            if dependency_errors:
                missing_modules = extract_missing_modules(dependency_errors)
                dep_output_lines = [
                    "Missing dependencies detected:",
                    f"  {len(dependency_errors)} dependency error(s)",
                ]
                if missing_modules:
                    modules_str = ", ".join(missing_modules[:10])
                    if len(missing_modules) > 10:
                        modules_str += f", ... (+{len(missing_modules) - 10} more)"
                    dep_output_lines.append(f"  Missing: {modules_str}")

                dep_output_lines.extend(
                    [
                        "",
                        "Suggestions:",
                        "  - Run 'bun install' or 'npm install' in your project",
                        "  - Use '--auto-install' flag to auto-install dependencies",
                        "  - If using Docker, ensure node_modules is available",
                    ],
                )

                # If there are also type errors, show both
                if type_errors:
                    dep_output_lines.insert(
                        0,
                        f"Type errors: {len(type_errors)}",
                    )
                    dep_output_lines.insert(1, "")

                # Return all issues but with helpful output
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output="\n".join(dep_output_lines),
                    issues_count=issues_count,
                    issues=all_issues,
                )

            if not success and issues_count == 0 and normalized_output:
                # Execution failed but no structured issues were parsed.
                # This can happen with malformed output or non-standard error formats.
                # Detect common dependency/configuration errors via substring matching
                # as a fallback when the parser couldn't extract structured issues.

                # Type definition errors (usually means node_modules not installed)
                if (
                    "Cannot find type definition file" in normalized_output
                    or "Cannot find module" in normalized_output
                ):
                    helpful_output = (
                        f"TypeScript configuration error:\n{normalized_output}\n\n"
                        "This usually means dependencies aren't installed.\n"
                        "Suggestions:\n"
                        "  - Run 'bun install' or 'npm install' in your project\n"
                        "  - Use '--auto-install' flag to auto-install dependencies\n"
                        "  - If using Docker, ensure node_modules is available\n"
                        "  - Use --tool-options 'tsc:skip_lib_check=true' to skip "
                        "type checking of declaration files"
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
                    output=normalized_output or "tsc execution failed.",
                    issues_count=0,
                )

            if not success and issues_count == 0:
                # No output - generic failure
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output="tsc execution failed.",
                    issues_count=0,
                )

            return ToolResult(
                name=self.definition.name,
                success=issues_count == 0,
                output=None,
                issues_count=issues_count,
                issues=all_issues,
            )
        finally:
            # Clean up temp tsconfig
            if temp_tsconfig and temp_tsconfig.exists():
                try:
                    temp_tsconfig.unlink()
                    logger.debug("[tsc] Cleaned up temp tsconfig: {}", temp_tsconfig)
                except OSError as e:
                    logger.warning("[tsc] Failed to clean up temp tsconfig: {}", e)

    def fix(self, paths: list[str], options: dict[str, object]) -> NoReturn:
        """Tsc does not support auto-fixing.

        Args:
            paths: Paths or files passed for completeness.
            options: Runtime options (unused).

        Raises:
            NotImplementedError: Always, because tsc cannot fix issues.
        """
        raise NotImplementedError(
            "Tsc cannot automatically fix issues. Type errors require "
            "manual code changes.",
        )
