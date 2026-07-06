"""Shared base for TypeScript-family type-checker tool definitions.

Both :mod:`lintro.tools.definitions.tsc` and
:mod:`lintro.tools.definitions.vue_tsc` drive a TypeScript compiler binary
(``tsc`` / ``vue-tsc``) with nearly identical orchestration:

- Command construction (``--noEmit --pretty false`` plus option flags).
- tsconfig discovery and temp-config file targeting
  (delegating to :mod:`lintro.utils.tsconfig`).
- Single- and multi-project execution.
- Output parsing, dependency-error categorization, and result shaping.

This module extracts that common shape into
:class:`TypeScriptCheckerPlugin`. Concrete tools subclass it and supply only
their per-tool deltas: the binary command, file extensions, parser wiring,
error-message copy, and (for ``tsc``) framework detection.

The refactor is behavior-preserving: subclasses that do not override the
optional hooks (framework detection, discovery-root computation) get exactly
the same behavior the standalone definitions had before extraction.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, NoReturn

from loguru import logger

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_parser import strip_ansi_codes
from lintro.plugins.base import BaseToolPlugin, ExecutionContext
from lintro.tools.core.option_validators import (
    OptionSchema,
    validate_option_types,
)
from lintro.tools.core.timeout_utils import create_timeout_result
from lintro.utils.tsconfig import (
    create_temp_tsconfig,
    discover_tsconfigs,
    has_explicit_scoping,
    partition_files,
    resolve_extends_chain,
)

# Expected types for the shared TypeScript-checker options, validated in
# ``set_options``. ``tsc`` and ``vue-tsc`` accept an identical option set.
_TS_CHECKER_OPTION_TYPES: OptionSchema = {
    "project": (str, "string path"),
    "strict": bool,
    "skip_lib_check": bool,
    "use_project_files": bool,
}


@dataclass
class TypeScriptCheckerPlugin(BaseToolPlugin):
    """Shared base for ``tsc``/``vue-tsc`` type-checker plugins.

    Subclasses provide their per-tool deltas via class variables and the
    small set of hook methods declared at the bottom of this class. The
    orchestration (command construction, tsconfig discovery, single- and
    multi-project execution, output parsing) lives here and is identical
    across the concrete tools.
    """

    # -- Per-tool class-level configuration (overridden by subclasses) -----
    #: Short label used in log prefixes and generic failure messages
    #: (e.g. ``"tsc"`` or ``"vue-tsc"``).
    _tool_label: ClassVar[str] = "ts-checker"
    #: Human-readable file kind used in debug logging (e.g. ``"TypeScript"``).
    _file_kind: ClassVar[str] = "TypeScript"
    #: Message emitted when no matching files are discovered.
    _no_files_message: ClassVar[str] = "No TypeScript files to check."
    #: Prefix used for temporary tsconfig files created for file targeting.
    _temp_config_prefix: ClassVar[str] = ".lintro-ts-"
    #: Message raised by :meth:`fix` (type checkers cannot auto-fix).
    _fix_error_message: ClassVar[str] = (
        "This tool cannot automatically fix issues. Type errors require "
        "manual code changes."
    )
    #: tsconfig filenames probed by :meth:`_find_tsconfig`, in priority order.
    _tsconfig_candidates: ClassVar[tuple[str, ...]] = ("tsconfig.json",)

    def set_options(
        self,
        project: str | None = None,
        strict: bool | None = None,
        skip_lib_check: bool | None = None,
        use_project_files: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set TypeScript-checker options.

        Args:
            project: Path to tsconfig.json file.
            strict: Enable strict type checking mode.
            skip_lib_check: Skip type checking of declaration files
                (default: True).
            use_project_files: When True, use tsconfig.json's include/files
                patterns instead of lintro's file targeting. Default is False,
                meaning lintro respects your file selection even when
                tsconfig.json exists.
            **kwargs: Other tool options.
        """
        options: dict[str, object] = {
            "project": project,
            "strict": strict,
            "skip_lib_check": skip_lib_check,
            "use_project_files": use_project_files,
        }
        validate_option_types(options, _TS_CHECKER_OPTION_TYPES)
        options = {k: v for k, v in options.items() if v is not None}
        super().set_options(**options, **kwargs)

    # -----------------------------------------------------------------
    # Command / config helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _resolve_binary_command(binary: str) -> list[str]:
        """Resolve the command used to invoke a TypeScript checker binary.

        Prefers the direct executable, then ``bunx``, then ``npx``, and
        finally falls back to the bare binary name in the hope it is on PATH.

        Args:
            binary: Name of the checker executable (e.g. ``"tsc"``).

        Returns:
            Command argument list for the checker.
        """
        # Prefer direct executable if available
        if shutil.which(binary):
            return [binary]
        # Try bunx (bun)
        if shutil.which("bunx"):
            return ["bunx", binary]
        # Try npx (npm)
        if shutil.which("npx"):
            return ["npx", binary]
        # Last resort - hope the binary is in PATH
        return [binary]

    def _find_tsconfig(self, cwd: Path) -> Path | None:
        """Find a tsconfig for the working directory or explicit project option.

        Args:
            cwd: Working directory to search for a tsconfig.

        Returns:
            Path to the tsconfig if found, None otherwise.
        """
        # Check explicit project option first
        project_opt = self.options.get("project")
        if project_opt and isinstance(project_opt, str):
            project_path = Path(project_opt)
            if project_path.is_absolute():
                return project_path if project_path.exists() else None
            resolved = cwd / project_path
            return resolved if resolved.exists() else None

        # Probe candidate tsconfig filenames in priority order
        for candidate in self._tsconfig_candidates:
            tsconfig = cwd / candidate
            if tsconfig.exists():
                return tsconfig
        return None

    def _create_temp_tsconfig(
        self,
        base_tsconfig: Path,
        files: list[str],
        cwd: Path,
    ) -> Path:
        """Create a temporary tsconfig.json that extends the base config.

        Delegates to the shared implementation in
        :func:`lintro.utils.tsconfig.create_temp_tsconfig`.

        Args:
            base_tsconfig: Path to the original tsconfig.json to extend.
            files: List of file paths to include (relative to cwd).
            cwd: Working directory for resolving paths.

        Returns:
            Path to the temporary tsconfig.json file.
        """
        return create_temp_tsconfig(
            base_tsconfig,
            files,
            cwd,
            prefix=self._temp_config_prefix,
            tool_label=self._tool_label,
        )

    def _build_command(
        self,
        files: list[str],
        project_path: str | Path | None = None,
        options: dict[str, object] | None = None,
    ) -> list[str]:
        """Build the checker invocation command.

        Args:
            files: Relative file paths (used only when no project config).
            project_path: Path to tsconfig.json to use (temp or user-specified).
            options: Options dict to use for flags. Defaults to self.options.

        Returns:
            A list of command arguments ready to be executed.
        """
        if options is None:
            options = self.options

        cmd: list[str] = list(self._command_prefix())

        # Core flags for linting (no output, machine-readable format)
        cmd.extend(["--noEmit", "--pretty", "false"])

        # Project flag (uses tsconfig.json - temp, explicit, or auto-discovered)
        if project_path:
            cmd.extend(["--project", str(project_path)])

        # Strict mode override (--strict is off by default)
        if options.get("strict") is True:
            cmd.append("--strict")

        # Skip lib check (faster, avoids issues with node_modules types)
        if options.get("skip_lib_check", True):
            cmd.append("--skipLibCheck")

        # Only pass files directly if no project config is being used
        if not project_path and files:
            cmd.extend(files)

        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return TypeScript error documentation URL.

        Uses typescript.tv, a third-party error reference, since the
        official TypeScript handbook does not provide per-error pages.

        Args:
            code: TypeScript error code (e.g., "TS2307" or "2307").

        Returns:
            URL to the TypeScript error documentation, or None if invalid.
        """
        if not code:
            return None
        # Strip "TS"/"ts" prefix if present to get the numeric portion
        upper = code.upper()
        num = code[2:] if upper.startswith("TS") else code
        if num.isdigit():
            return DocUrlTemplate.TSC.format(code=num)
        return None

    # -----------------------------------------------------------------
    # Check orchestration
    # -----------------------------------------------------------------

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with the TypeScript checker.

        By default, lintro respects your file selection even when
        tsconfig.json exists, by creating a temporary tsconfig that extends
        your project's config but targets only the specified files. Set
        ``use_project_files=True`` to use native tsconfig file selection.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Use shared preparation for version check, path validation, discovery
        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message=self._no_files_message,
        )

        if ctx.should_skip and ctx.early_result is not None:
            return ctx.early_result

        # Safety check: if should_skip but no early_result, create one
        if ctx.should_skip:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output=self._no_files_message,
                issues_count=0,
            )

        logger.debug(
            "[{}] Discovered {} {} file(s)",
            self._tool_label,
            len(ctx.files),
            self._file_kind,
        )

        # Determine project configuration strategy
        cwd_path = Path(ctx.cwd) if ctx.cwd else Path.cwd()

        # Check if dependencies need installing
        from lintro.utils.node_deps import install_node_deps, should_install_deps

        try:
            needs_install = should_install_deps(cwd_path)
        except PermissionError as e:
            logger.warning("[{}] {}", self._tool_label, e)
            return ToolResult(
                name=self.definition.name,
                success=True,
                output=f"Skipping {self._tool_label}: {e}",
                issues_count=0,
                skipped=True,
                skip_reason="directory not writable",
            )

        if needs_install:
            auto_install = merged_options.get("auto_install", False)
            if auto_install:
                logger.info(
                    "[{}] Auto-installing Node.js dependencies...",
                    self._tool_label,
                )
                install_ok, install_output = install_node_deps(cwd_path)
                if install_ok:
                    logger.info(
                        "[{}] Dependencies installed successfully",
                        self._tool_label,
                    )
                else:
                    logger.warning(
                        "[{}] Auto-install failed, skipping: {}",
                        self._tool_label,
                        install_output,
                    )
                    return ToolResult(
                        name=self.definition.name,
                        success=True,
                        output=(
                            f"Skipping {self._tool_label}: auto-install failed.\n"
                            f"{install_output}"
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

        # Bypass partitioning when user explicitly controls the config
        if use_project_files or explicit_project:
            return self._check_single_project(
                ctx,
                cwd_path,
                merged_options,
                use_project_files=True,
                explicit_project=explicit_project,
            )

        # Compute the discovery root (per-tool: cwd by default, common
        # ancestor of input paths for tsc's multi-package support).
        discovery_root = self._compute_discovery_root(cwd_path, paths)

        # Discover tsconfigs for multi-project support
        tsconfigs = discover_tsconfigs(discovery_root, self.exclude_patterns)

        if len(tsconfigs) > 1:
            return self._check_multi_project(ctx, cwd_path, tsconfigs, merged_options)

        # Pass the discovered tsconfig (if any) so _check_single_project
        # doesn't have to re-discover it from a potentially different cwd.
        discovered_tsconfig = tsconfigs[0].path if tsconfigs else None
        return self._check_single_project(
            ctx,
            cwd_path,
            merged_options,
            discovered_tsconfig=discovered_tsconfig,
        )

    # -----------------------------------------------------------------
    # Single-project check
    # -----------------------------------------------------------------

    def _check_single_project(
        self,
        ctx: ExecutionContext,
        cwd_path: Path,
        options: dict[str, object],
        *,
        use_project_files: bool = False,
        explicit_project: str | None = None,
        discovered_tsconfig: Path | None = None,
    ) -> ToolResult:
        """Run the checker against a single project.

        Args:
            ctx: Prepared execution context with discovered files.
            cwd_path: Working directory.
            options: Merged runtime options.
            use_project_files: Use native tsconfig file selection.
            explicit_project: Explicit ``--project`` path.
            discovered_tsconfig: Pre-discovered tsconfig path from
                :func:`discover_tsconfigs`, avoiding re-discovery from a
                potentially different cwd.

        Returns:
            ToolResult with check results.
        """
        temp_tsconfig: Path | None = None
        project_path: str | None = None

        try:
            # Resolve explicit_project first so framework detection runs
            # against the project the user pointed at, not cwd_path.
            base_tsconfig: Path | None = discovered_tsconfig
            if base_tsconfig is None and explicit_project:
                explicit_path = Path(explicit_project)
                if not explicit_path.is_absolute():
                    explicit_path = (cwd_path / explicit_path).resolve()
                if explicit_path.exists():
                    base_tsconfig = explicit_path
            if base_tsconfig is None:
                base_tsconfig = self._find_tsconfig(cwd_path)

            # Per-project framework detection. Use the tsconfig's project_dir
            # when available (so a sub-project nested under cwd is still
            # detected), falling back to cwd_path when no tsconfig was found.
            # Tools without framework detection return None here (no-op).
            detection_dir = base_tsconfig.parent if base_tsconfig else cwd_path
            framework_info = self._detect_framework_project(detection_dir)
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

            if use_project_files or explicit_project:
                project_path = explicit_project or (
                    str(base_tsconfig) if base_tsconfig else None
                )
                logger.debug(
                    "[{}] Using native tsconfig file selection: {}",
                    self._tool_label,
                    project_path,
                )
            elif base_tsconfig:
                # Issue #851: respect tsconfig include/exclude/files scoping.
                # When the tsconfig has explicit scoping, run the checker -p
                # directly instead of creating a temp tsconfig that overrides
                # include.
                tsconfig_info = resolve_extends_chain(base_tsconfig)
                if has_explicit_scoping(tsconfig_info):
                    project_path = str(base_tsconfig)
                    logger.info(
                        "[{}] Respecting native tsconfig scoping: {}",
                        self._tool_label,
                        base_tsconfig,
                    )
                else:
                    temp_tsconfig = self._create_temp_tsconfig(
                        base_tsconfig=base_tsconfig,
                        files=ctx.rel_files,
                        cwd=cwd_path,
                    )
                    project_path = str(temp_tsconfig)
                    logger.debug(
                        "[{}] Using temp tsconfig for file targeting: {}",
                        self._tool_label,
                        project_path,
                    )
            else:
                project_path = None
                logger.debug(
                    "[{}] No tsconfig.json found, passing files directly",
                    self._tool_label,
                )

            return self._run_and_parse(
                ctx=ctx,
                project_path=project_path,
                options=options,
            )
        finally:
            if temp_tsconfig and temp_tsconfig.exists():
                try:
                    temp_tsconfig.unlink()
                    logger.debug(
                        "[{}] Cleaned up temp tsconfig: {}",
                        self._tool_label,
                        temp_tsconfig,
                    )
                except OSError as e:
                    logger.warning(
                        "[{}] Failed to clean up temp tsconfig: {}",
                        self._tool_label,
                        e,
                    )

    # -----------------------------------------------------------------
    # Multi-project check
    # -----------------------------------------------------------------

    def _check_multi_project(
        self,
        ctx: ExecutionContext,
        cwd_path: Path,
        tsconfigs: list[Any],
        options: dict[str, object],
    ) -> ToolResult:
        """Run the checker against each discovered sub-project and aggregate.

        Args:
            ctx: Prepared execution context with discovered files.
            cwd_path: Working directory (monorepo root).
            tsconfigs: Discovered TsconfigInfo objects, deepest-first.
            options: Merged runtime options.

        Returns:
            Aggregated ToolResult across all sub-projects.
        """
        import os

        partitions = partition_files(ctx.files, tsconfigs, log_label=self._tool_label)

        all_issues: list[Any] = []
        output_sections: list[str] = []
        temp_files: list[Path] = []
        any_succeeded = False
        had_subproject_error = False

        try:
            for tsconfig_info, project_files in partitions:
                if not project_files and tsconfig_info is not None:
                    continue

                project_dir = tsconfig_info.project_dir if tsconfig_info else cwd_path

                # Per-project framework detection (no-op for tools without it)
                if tsconfig_info is not None:
                    framework_info = self._detect_framework_project(project_dir)
                    if framework_info:
                        framework_name, recommended_tool = framework_info
                        try:
                            rel = project_dir.relative_to(cwd_path)
                        except ValueError:
                            rel = project_dir
                        output_sections.append(
                            f"── {rel} ──\n"
                            f"  Skipped: {framework_name} project "
                            f"(use {recommended_tool})",
                        )
                        # An intentional skip is not a failure — mark as
                        # succeeded so an all-framework monorepo doesn't
                        # report as failed.
                        any_succeeded = True
                        continue

                # Determine project_path for this sub-project
                temp_tsconfig: Path | None = None
                project_path: str | None = None

                if tsconfig_info is not None and has_explicit_scoping(tsconfig_info):
                    project_path = str(tsconfig_info.path)
                elif tsconfig_info is not None:
                    rel_files = [os.path.relpath(f, project_dir) for f in project_files]
                    temp_tsconfig = self._create_temp_tsconfig(
                        tsconfig_info.path,
                        rel_files,
                        project_dir,
                    )
                    temp_files.append(temp_tsconfig)
                    project_path = str(temp_tsconfig)

                # Build and run
                cmd = self._build_command(
                    files=(
                        [os.path.relpath(f, project_dir) for f in project_files]
                        if not project_path
                        else []
                    ),
                    project_path=project_path,
                    options=options,
                )

                try:
                    proc_success, output = self._run_subprocess(
                        cmd=cmd,
                        timeout=ctx.timeout,
                        cwd=str(project_dir),
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                    logger.warning(
                        "[{}] Sub-project {} failed: {}",
                        self._tool_label,
                        project_dir,
                        e,
                    )
                    had_subproject_error = True
                    continue

                if proc_success:
                    any_succeeded = True
                else:
                    had_subproject_error = True
                issues = self._parse_output(output or "")
                all_issues.extend(issues)

                try:
                    rel_project = project_dir.relative_to(cwd_path)
                except ValueError:
                    rel_project = project_dir
                count = len(issues)
                section = (
                    f"── {rel_project} "
                    f"({count} issue{'s' if count != 1 else ''}) ──"
                )
                output_sections.append(section)

            total_issues = len(all_issues)
            output_text = "\n".join(output_sections) if output_sections else None
            success = any_succeeded and not had_subproject_error and total_issues == 0
            return ToolResult(
                name=self.definition.name,
                success=success,
                output=output_text,
                issues_count=total_issues,
                issues=all_issues,
            )
        finally:
            for temp in temp_files:
                try:
                    if temp.exists():
                        temp.unlink()
                except OSError:
                    pass

    # -----------------------------------------------------------------
    # Shared execution + output parsing
    # -----------------------------------------------------------------

    def _run_and_parse(
        self,
        ctx: ExecutionContext,
        project_path: str | None,
        options: dict[str, object],
    ) -> ToolResult:
        """Build the checker command, run it, and parse the output.

        Args:
            ctx: Prepared execution context.
            project_path: ``--project`` path or ``None``.
            options: Merged runtime options.

        Returns:
            ToolResult with parsed issues.
        """
        cmd = self._build_command(
            files=ctx.rel_files if not project_path else [],
            project_path=project_path,
            options=options,
        )
        logger.debug(
            "[{}] Running with cwd={} and cmd={}",
            self._tool_label,
            ctx.cwd,
            cmd,
        )

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
                output=self._not_found_output(e),
                issues_count=0,
            )
        except OSError as e:
            logger.error(
                "[{}] Failed to run {}: {}",
                self._tool_label,
                self._tool_label,
                e,
            )
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"{self._tool_label} execution failed: " + str(e),
                issues_count=0,
            )

        # Parse output (parser handles ANSI stripping internally)
        all_issues = self._parse_output(output or "")
        issues_count = len(all_issues)

        # Normalize output for fallback substring matching below
        normalized_output = strip_ansi_codes(output) if output else ""

        # Categorize issues into type errors vs dependency errors
        type_errors, dependency_errors = self._categorize_issues(all_issues)

        # If we have dependency errors, provide helpful guidance
        if dependency_errors:
            missing_modules = self._extract_missing_modules(dependency_errors)
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
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=self._config_error_output(normalized_output),
                    issues_count=0,
                )

            return ToolResult(
                name=self.definition.name,
                success=False,
                output=normalized_output or f"{self._tool_label} execution failed.",
                issues_count=0,
            )

        if not success and issues_count == 0:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"{self._tool_label} execution failed.",
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
        """Type checkers do not support auto-fixing.

        Args:
            paths: Paths or files passed for completeness.
            options: Runtime options (unused).

        Raises:
            NotImplementedError: Always, because type checkers cannot fix
                issues.
        """
        raise NotImplementedError(self._fix_error_message)

    # -----------------------------------------------------------------
    # Hooks: subclasses supply per-tool deltas
    # -----------------------------------------------------------------

    def _command_prefix(self) -> list[str]:
        """Return the command prefix used to invoke the checker.

        Returns:
            Command argument list (e.g. ``["tsc"]`` or ``["bunx", "vue-tsc"]``).

        Raises:
            NotImplementedError: If a subclass does not override this hook.
        """
        raise NotImplementedError

    def _parse_output(self, output: str) -> list[Any]:
        """Parse raw checker output into structured issues.

        Args:
            output: Raw stdout/stderr text from the checker.

        Returns:
            List of parsed issue objects.

        Raises:
            NotImplementedError: If a subclass does not override this hook.
        """
        raise NotImplementedError

    def _categorize_issues(
        self,
        issues: list[Any],
    ) -> tuple[list[Any], list[Any]]:
        """Split issues into (type errors, dependency errors).

        Args:
            issues: Parsed issue objects.

        Returns:
            A ``(type_errors, dependency_errors)`` tuple.

        Raises:
            NotImplementedError: If a subclass does not override this hook.
        """
        raise NotImplementedError

    def _extract_missing_modules(self, dependency_errors: list[Any]) -> list[str]:
        """Extract missing module names from dependency errors.

        Args:
            dependency_errors: Dependency-related issue objects.

        Returns:
            List of missing module names.

        Raises:
            NotImplementedError: If a subclass does not override this hook.
        """
        raise NotImplementedError

    def _not_found_output(self, error: FileNotFoundError) -> str:
        """Build the output shown when the checker binary is not found.

        Args:
            error: The FileNotFoundError raised while launching the checker.

        Returns:
            User-facing guidance text.

        Raises:
            NotImplementedError: If a subclass does not override this hook.
        """
        raise NotImplementedError

    def _config_error_output(self, normalized_output: str) -> str:
        """Build the output shown for a likely dependency/config error.

        Args:
            normalized_output: ANSI-stripped checker output.

        Returns:
            User-facing guidance text.

        Raises:
            NotImplementedError: If a subclass does not override this hook.
        """
        raise NotImplementedError

    def _detect_framework_project(self, cwd: Path) -> tuple[str, str] | None:
        """Detect a framework project that owns its own type checker.

        The default implementation performs no detection. ``tsc`` overrides
        this to defer to framework-specific checkers (astro-check, vue-tsc,
        svelte-check).

        Args:
            cwd: Directory to inspect for framework config files.

        Returns:
            A ``(framework_name, recommended_tool)`` tuple if a framework is
            detected, otherwise None.
        """
        return None

    def _compute_discovery_root(self, cwd_path: Path, paths: list[str]) -> Path:
        """Compute the root directory used for tsconfig discovery.

        The default returns ``cwd_path``. ``tsc`` overrides this to use the
        common ancestor of all input paths so that tsconfigs in sibling
        packages are discovered when multiple paths are provided.

        Args:
            cwd_path: The prepared execution working directory.
            paths: The original input paths.

        Returns:
            Directory to scan for tsconfigs.
        """
        return cwd_path
