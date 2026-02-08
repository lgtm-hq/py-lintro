"""Vue-tsc tool definition.

Vue-tsc is the TypeScript type checker for Vue Single File Components (SFCs).
This enables proper type checking for `.vue` files that regular `tsc` cannot
handle.

Example:
    # Check Vue project
    lintro check src/ --tools vue-tsc

    # With specific config
    lintro check src/ --tools vue-tsc --tool-options "vue-tsc:project=tsconfig.app.json"
"""

from __future__ import annotations

import functools
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
from lintro.parsers.vue_tsc.vue_tsc_parser import (
    categorize_vue_tsc_issues,
    extract_missing_modules,
    parse_vue_tsc_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.timeout_utils import create_timeout_result
from lintro.utils.jsonc import load_jsonc

# Constants for Vue-tsc configuration
VUE_TSC_DEFAULT_TIMEOUT: int = 120
VUE_TSC_DEFAULT_PRIORITY: int = 83  # After tsc (82)
VUE_TSC_FILE_PATTERNS: list[str] = ["*.vue"]


@register_tool
@dataclass
class VueTscPlugin(BaseToolPlugin):
    """Vue-tsc type checking plugin.

    This plugin integrates vue-tsc with Lintro for static type checking
    of Vue Single File Components.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="vue-tsc",
            description="Vue TypeScript type checker for Vue SFC diagnostics",
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.TYPE_CHECKER,
            file_patterns=VUE_TSC_FILE_PATTERNS,
            priority=VUE_TSC_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["tsconfig.json", "tsconfig.app.json"],
            version_command=self._vue_tsc_cmd + ["--version"],
            min_version=get_min_version(ToolName.VUE_TSC),
            default_options={
                "timeout": VUE_TSC_DEFAULT_TIMEOUT,
                "project": None,
                "strict": None,
                "skip_lib_check": True,
                "use_project_files": False,
            },
            default_timeout=VUE_TSC_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        project: str | None = None,
        strict: bool | None = None,
        skip_lib_check: bool | None = None,
        use_project_files: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set vue-tsc-specific options.

        Args:
            project: Path to tsconfig.json file.
            strict: Enable strict type checking mode.
            skip_lib_check: Skip type checking of declaration files (default: True).
            use_project_files: When True, use tsconfig.json's include/files patterns
                instead of lintro's file targeting. Default is False.
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

    @functools.cached_property
    def _vue_tsc_cmd(self) -> list[str]:
        """Get the command to run vue-tsc.

        Prefers direct vue-tsc executable, falls back to bunx/npx.
        The result is cached so that repeated accesses (e.g. from the
        ``definition`` property and ``_build_command``) reuse the stored
        command without repeated ``shutil.which()`` lookups.

        Returns:
            Command arguments for vue-tsc.
        """
        # Prefer direct executable if available
        if shutil.which("vue-tsc"):
            return ["vue-tsc"]
        # Try bunx (bun)
        if shutil.which("bunx"):
            return ["bunx", "vue-tsc"]
        # Try npx (npm)
        if shutil.which("npx"):
            return ["npx", "vue-tsc"]
        # Last resort
        return ["vue-tsc"]

    def _find_tsconfig(self, cwd: Path) -> Path | None:
        """Find tsconfig.json in the working directory.

        Checks for both tsconfig.json and tsconfig.app.json (Vite projects).

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

        # Check for tsconfig.app.json first (Vite Vue projects)
        tsconfig_app = cwd / "tsconfig.app.json"
        if tsconfig_app.exists():
            return tsconfig_app

        # Check for tsconfig.json
        tsconfig = cwd / "tsconfig.json"
        return tsconfig if tsconfig.exists() else None

    def _create_temp_tsconfig(
        self,
        base_tsconfig: Path,
        files: list[str],
        cwd: Path,
    ) -> Path:
        """Create a temporary tsconfig.json that extends the base config.

        Args:
            base_tsconfig: Path to the original tsconfig.json to extend.
            files: List of file paths to include (relative to cwd).
            cwd: Working directory for resolving paths.

        Returns:
            Path to the temporary tsconfig.json file.

        Raises:
            OSError: If writing the temporary file fails.
        """
        abs_base = base_tsconfig.resolve()
        abs_files = [str((cwd / f).resolve()) for f in files]

        compiler_options: dict[str, Any] = {
            "noEmit": True,
        }

        # Read typeRoots from the base tsconfig so they are preserved in the
        # temp config.  TypeScript resolves typeRoots relative to the config
        # file, so we resolve them to absolute paths here because the temp
        # config lives in a different directory.
        try:
            base_content = load_jsonc(abs_base.read_text(encoding="utf-8"))
            if isinstance(base_content, dict):
                comp_opts = base_content.get("compilerOptions")
                if isinstance(comp_opts, dict):
                    base_roots = comp_opts.get("typeRoots")
                    if isinstance(base_roots, list):
                        resolved_roots: list[str] = []
                        for r in base_roots:
                            if not isinstance(r, str):
                                continue
                            try:
                                resolved_roots.append(
                                    str((abs_base.parent / r).resolve()),
                                )
                            except (ValueError, OSError):
                                continue
                        if resolved_roots:
                            compiler_options["typeRoots"] = resolved_roots
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(
                "[vue-tsc] Could not read typeRoots from {}: {}",
                abs_base,
                exc,
            )

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
                prefix=".lintro-vue-tsc-",
                dir=abs_base.parent,
            )
        except OSError:
            fd, temp_path = tempfile.mkstemp(
                suffix=".json",
                prefix="lintro-vue-tsc-",
            )
            # Preserve existing typeRoots from the base tsconfig and add
            # the default node_modules/@types path so TypeScript can still
            # resolve type packages from the system temp dir.
            existing_type_roots: list[str] = []
            try:
                base_content = load_jsonc(
                    base_tsconfig.read_text(encoding="utf-8"),
                )
                if isinstance(base_content, dict):
                    comp_opts = base_content.get("compilerOptions")
                    if isinstance(comp_opts, dict):
                        base_roots = comp_opts.get("typeRoots")
                        if isinstance(base_roots, list):
                            for root in base_roots:
                                if not isinstance(root, str):
                                    continue
                                try:
                                    existing_type_roots.append(
                                        str((abs_base.parent / root).resolve()),
                                    )
                                except (ValueError, OSError):
                                    continue
            except (json.JSONDecodeError, OSError):
                pass
            default_root = str(cwd / "node_modules" / "@types")
            if default_root not in existing_type_roots:
                existing_type_roots.append(default_root)
            compiler_options["typeRoots"] = existing_type_roots

        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(temp_config, f, indent=2)
        except OSError:
            Path(temp_path).unlink(missing_ok=True)
            raise

        logger.debug(
            "[vue-tsc] Created temp tsconfig at {} extending {} with {} files",
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
        """Build the vue-tsc invocation command.

        Args:
            files: Relative file paths (used only when no project config).
            project_path: Path to tsconfig.json to use.
            options: Options dict to use for flags. Defaults to self.options.

        Returns:
            A list of command arguments ready to be executed.
        """
        if options is None:
            options = self.options

        cmd: list[str] = list(self._vue_tsc_cmd)

        # Core flags for type checking only
        cmd.extend(["--noEmit", "--pretty", "false"])

        # Project flag
        if project_path:
            cmd.extend(["--project", str(project_path)])

        # Strict mode override
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
        """Check files with vue-tsc.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Use shared preparation
        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No Vue files to check.",
        )

        if ctx.should_skip and ctx.early_result is not None:
            return ctx.early_result

        if ctx.should_skip:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No Vue files to check.",
                issues_count=0,
            )

        logger.debug("[vue-tsc] Discovered {} Vue file(s)", len(ctx.files))

        cwd_path = Path(ctx.cwd) if ctx.cwd else Path.cwd()

        # Check if dependencies need installing
        from lintro.utils.node_deps import install_node_deps, should_install_deps

        if should_install_deps(cwd_path):
            auto_install = merged_options.get("auto_install", False)
            if auto_install:
                logger.info("[vue-tsc] Auto-installing Node.js dependencies...")
                install_ok, install_output = install_node_deps(cwd_path)
                if install_ok:
                    logger.info("[vue-tsc] Dependencies installed successfully")
                else:
                    logger.warning(
                        "[vue-tsc] Auto-install failed, skipping: {}",
                        install_output,
                    )
                    return ToolResult(
                        name=self.definition.name,
                        success=True,
                        output=(
                            f"Skipping vue-tsc: auto-install failed.\n"
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

        use_project_files = merged_options.get("use_project_files", False)
        explicit_project_opt = merged_options.get("project")
        explicit_project = str(explicit_project_opt) if explicit_project_opt else None
        temp_tsconfig: Path | None = None
        project_path: str | None = None

        try:
            # Find existing tsconfig.json
            base_tsconfig = self._find_tsconfig(cwd_path)

            if use_project_files or explicit_project:
                project_path = explicit_project or (
                    str(base_tsconfig) if base_tsconfig else None
                )
                logger.debug(
                    "[vue-tsc] Using native tsconfig file selection: {}",
                    project_path,
                )
            elif base_tsconfig:
                temp_tsconfig = self._create_temp_tsconfig(
                    base_tsconfig=base_tsconfig,
                    files=ctx.rel_files,
                    cwd=cwd_path,
                )
                project_path = str(temp_tsconfig)
                logger.debug(
                    "[vue-tsc] Using temp tsconfig for file targeting: {}",
                    project_path,
                )
            else:
                project_path = None
                logger.debug(
                    "[vue-tsc] No tsconfig.json found, passing files directly",
                )

            # Build command
            cmd = self._build_command(
                files=ctx.rel_files if not project_path else [],
                project_path=project_path,
                options=merged_options,
            )
            logger.debug("[vue-tsc] Running with cwd={} and cmd={}", ctx.cwd, cmd)

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
                    output=f"vue-tsc not found: {e}\n\n"
                    "Please ensure vue-tsc is installed:\n"
                    "  - Run 'bun add -D vue-tsc' or 'npm install -D vue-tsc'\n"
                    "  - Or install globally: 'bun add -g vue-tsc'",
                    issues_count=0,
                )
            except OSError as e:
                logger.error("[vue-tsc] Failed to run vue-tsc: {}", e)
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output="vue-tsc execution failed: " + str(e),
                    issues_count=0,
                )

            # Parse output
            all_issues = parse_vue_tsc_output(output=output or "")
            issues_count = len(all_issues)

            normalized_output = strip_ansi_codes(output) if output else ""

            # Categorize issues
            type_errors, dependency_errors = categorize_vue_tsc_issues(all_issues)

            # Handle dependency errors
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

                if type_errors:
                    dep_output_lines.insert(0, f"Type errors: {len(type_errors)}")
                    dep_output_lines.insert(1, "")

                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output="\n".join(dep_output_lines),
                    issues_count=issues_count,
                    issues=all_issues,
                )

            if not success and issues_count == 0 and normalized_output:
                if (
                    "Cannot find type definition file" in normalized_output
                    or "Cannot find module" in normalized_output
                ):
                    helpful_output = (
                        f"vue-tsc configuration error:\n{normalized_output}\n\n"
                        "This usually means dependencies aren't installed.\n"
                        "Suggestions:\n"
                        "  - Run 'bun install' or 'npm install' in your project\n"
                        "  - Use '--auto-install' flag to auto-install dependencies\n"
                        "  - If using Docker, ensure node_modules is available"
                    )
                    return ToolResult(
                        name=self.definition.name,
                        success=False,
                        output=helpful_output,
                        issues_count=0,
                    )

                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=normalized_output or "vue-tsc execution failed.",
                    issues_count=0,
                )

            if not success and issues_count == 0:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output="vue-tsc execution failed.",
                    issues_count=0,
                )

            return ToolResult(
                name=self.definition.name,
                success=success and issues_count == 0,
                output=None,
                issues_count=issues_count,
                issues=all_issues,
            )
        finally:
            # Clean up temp tsconfig
            if temp_tsconfig and temp_tsconfig.exists():
                try:
                    temp_tsconfig.unlink()
                    logger.debug(
                        "[vue-tsc] Cleaned up temp tsconfig: {}",
                        temp_tsconfig,
                    )
                except OSError as e:
                    logger.warning(
                        "[vue-tsc] Failed to clean up temp tsconfig: {}",
                        e,
                    )

    def fix(self, paths: list[str], options: dict[str, object]) -> NoReturn:
        """Vue-tsc does not support auto-fixing.

        Args:
            paths: Paths or files passed for completeness.
            options: Runtime options (unused).

        Raises:
            NotImplementedError: Always, because vue-tsc cannot fix issues.
        """
        raise NotImplementedError(
            "vue-tsc cannot automatically fix issues. Type errors require "
            "manual code changes.",
        )
