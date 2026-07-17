"""Check orchestration helpers for TypeScript-checker plugins."""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_parser import strip_ansi_codes
from lintro.plugins.base import ExecutionContext
from lintro.tools.core.timeout_utils import create_timeout_result
from lintro.utils.tsconfig import (
    discover_tsconfigs,
    has_explicit_scoping,
    partition_files,
    resolve_extends_chain,
)

if TYPE_CHECKING:
    from lintro.tools.definitions._ts_checker_base import TypeScriptCheckerPlugin


def check(
    plugin: TypeScriptCheckerPlugin,
    paths: list[str],
    options: dict[str, object],
) -> ToolResult:
    """Check files with the TypeScript checker.

    By default, lintro respects your file selection even when
    tsconfig.json exists, by creating a temporary tsconfig that extends
    your project's config but targets only the specified files. Set
    ``use_project_files=True`` to use native tsconfig file selection.

    Args:
        plugin: TypeScript-checker plugin instance.
        paths: List of file or directory paths to check.
        options: Runtime options that override defaults.

    Returns:
        ToolResult with check results.
    """
    # Merge runtime options
    merged_options = dict(plugin.options)
    merged_options.update(options)

    # Use shared preparation for version check, path validation, discovery
    ctx = plugin._prepare_execution(
        paths,
        merged_options,
        no_files_message=plugin._no_files_message,
    )

    if ctx.should_skip and ctx.early_result is not None:
        return ctx.early_result

    # Safety check: if should_skip but no early_result, create one
    if ctx.should_skip:
        return ToolResult(
            name=plugin.definition.name,
            success=True,
            output=plugin._no_files_message,
            issues_count=0,
        )

    logger.debug(
        "[{}] Discovered {} {} file(s)",
        plugin._tool_label,
        len(ctx.files),
        plugin._file_kind,
    )

    # Determine project configuration strategy
    cwd_path = Path(ctx.cwd) if ctx.cwd else Path.cwd()

    # Check if dependencies need installing
    from lintro.utils.node_deps import install_node_deps, should_install_deps

    try:
        needs_install = should_install_deps(cwd_path)
    except PermissionError as e:
        logger.warning("[{}] {}", plugin._tool_label, e)
        return ToolResult(
            name=plugin.definition.name,
            success=True,
            output=f"Skipping {plugin._tool_label}: {e}",
            issues_count=0,
            skipped=True,
            skip_reason="directory not writable",
        )

    if needs_install:
        auto_install = merged_options.get("auto_install", False)
        if auto_install:
            logger.info(
                "[{}] Auto-installing Node.js dependencies...",
                plugin._tool_label,
            )
            install_ok, install_output = install_node_deps(cwd_path)
            if install_ok:
                logger.info(
                    "[{}] Dependencies installed successfully",
                    plugin._tool_label,
                )
            else:
                logger.warning(
                    "[{}] Auto-install failed, skipping: {}",
                    plugin._tool_label,
                    install_output,
                )
                return ToolResult(
                    name=plugin.definition.name,
                    success=True,
                    output=(
                        f"Skipping {plugin._tool_label}: auto-install failed.\n"
                        f"{install_output}"
                    ),
                    issues_count=0,
                    skipped=True,
                    skip_reason="auto-install failed",
                )
        else:
            return ToolResult(
                name=plugin.definition.name,
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
        return _check_single_project(
            plugin,
            ctx,
            cwd_path,
            merged_options,
            use_project_files=True,
            explicit_project=explicit_project,
        )

    # Compute the discovery root (per-tool: cwd by default, common
    # ancestor of input paths for tsc's multi-package support).
    discovery_root = plugin._compute_discovery_root(cwd_path, paths)

    # Discover tsconfigs for multi-project support. When the discovered
    # configs span several directories the inputs cover multiple packages,
    # and multi-project partitioning must keep precedence: each file is
    # checked against its nearest package config, so a root-level
    # preferred config must not short-circuit and route child-package
    # files through the root app config.
    tsconfigs = discover_tsconfigs(discovery_root, plugin.exclude_patterns)
    distinct_dirs = {info.path.parent.resolve() for info in tsconfigs}
    if len(distinct_dirs) > 1:
        return _check_multi_project(plugin, ctx, cwd_path, tsconfigs, merged_options)

    # Single project directory: respect the subclass's preferred tsconfig
    # ordering. A subclass (e.g. VueTscPlugin) may declare a
    # framework-specific config such as ``tsconfig.app.json`` ahead of
    # ``tsconfig.json``; when that preferred config is present it must win
    # over generic discovery, which would otherwise select
    # ``tsconfig.json`` and bypass the Vue preference (issue #1112).
    # ``tsc`` — whose sole candidate is ``tsconfig.json`` — is unaffected.
    preferred_tsconfig = plugin._preferred_candidate_tsconfig(discovery_root)
    if preferred_tsconfig is not None:
        logger.debug(
            "[{}] Using preferred tsconfig ahead of discovery: {}",
            plugin._tool_label,
            preferred_tsconfig,
        )
        return _check_single_project(
            plugin,
            ctx,
            cwd_path,
            merged_options,
            discovered_tsconfig=preferred_tsconfig,
        )

    if len(tsconfigs) > 1:
        return _check_multi_project(plugin, ctx, cwd_path, tsconfigs, merged_options)

    # Pass the discovered tsconfig (if any) so _check_single_project
    # doesn't have to re-discover it from a potentially different cwd.
    discovered_tsconfig = tsconfigs[0].path if tsconfigs else None
    return _check_single_project(
        plugin,
        ctx,
        cwd_path,
        merged_options,
        discovered_tsconfig=discovered_tsconfig,
    )


def _check_single_project(
    plugin: TypeScriptCheckerPlugin,
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
        plugin: TypeScript-checker plugin instance.
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
            base_tsconfig = plugin._find_tsconfig(cwd_path)

        # Per-project framework detection. Use the tsconfig's project_dir
        # when available (so a sub-project nested under cwd is still
        # detected), falling back to cwd_path when no tsconfig was found.
        # Tools without framework detection return None here (no-op).
        detection_dir = base_tsconfig.parent if base_tsconfig else cwd_path
        framework_info = plugin._detect_framework_project(detection_dir)
        if framework_info:
            framework_name, recommended_tool = framework_info
            return ToolResult(
                name=plugin.definition.name,
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
                plugin._tool_label,
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
                    plugin._tool_label,
                    base_tsconfig,
                )
            else:
                temp_tsconfig = plugin._create_temp_tsconfig(
                    base_tsconfig=base_tsconfig,
                    files=ctx.rel_files,
                    cwd=cwd_path,
                )
                project_path = str(temp_tsconfig)
                logger.debug(
                    "[{}] Using temp tsconfig for file targeting: {}",
                    plugin._tool_label,
                    project_path,
                )
        else:
            project_path = None
            logger.debug(
                "[{}] No tsconfig.json found, passing files directly",
                plugin._tool_label,
            )

        return _run_and_parse(
            plugin,
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
                    plugin._tool_label,
                    temp_tsconfig,
                )
            except OSError as e:
                logger.warning(
                    "[{}] Failed to clean up temp tsconfig: {}",
                    plugin._tool_label,
                    e,
                )


def _check_multi_project(
    plugin: TypeScriptCheckerPlugin,
    ctx: ExecutionContext,
    cwd_path: Path,
    tsconfigs: list[Any],
    options: dict[str, object],
) -> ToolResult:
    """Run the checker against each discovered sub-project and aggregate.

    Args:
        plugin: TypeScript-checker plugin instance.
        ctx: Prepared execution context with discovered files.
        cwd_path: Working directory (monorepo root).
        tsconfigs: Discovered TsconfigInfo objects, deepest-first.
        options: Merged runtime options.

    Returns:
        Aggregated ToolResult across all sub-projects.
    """
    import os

    partitions = partition_files(ctx.files, tsconfigs, log_label=plugin._tool_label)

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
                framework_info = plugin._detect_framework_project(project_dir)
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
                temp_tsconfig = plugin._create_temp_tsconfig(
                    tsconfig_info.path,
                    rel_files,
                    project_dir,
                )
                temp_files.append(temp_tsconfig)
                project_path = str(temp_tsconfig)

            # Build and run
            cmd = plugin._build_command(
                files=(
                    [os.path.relpath(f, project_dir) for f in project_files]
                    if not project_path
                    else []
                ),
                project_path=project_path,
                options=options,
            )

            try:
                proc_success, output = plugin._run_subprocess(
                    cmd=cmd,
                    timeout=ctx.timeout,
                    cwd=str(project_dir),
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                logger.warning(
                    "[{}] Sub-project {} failed: {}",
                    plugin._tool_label,
                    project_dir,
                    e,
                )
                had_subproject_error = True
                continue

            if proc_success:
                any_succeeded = True
            else:
                had_subproject_error = True
            issues = plugin._parse_output(output or "")
            all_issues.extend(issues)

            try:
                rel_project = project_dir.relative_to(cwd_path)
            except ValueError:
                rel_project = project_dir
            count = len(issues)
            section = f"── {rel_project} ({count} issue{'s' if count != 1 else ''}) ──"
            output_sections.append(section)

        total_issues = len(all_issues)
        output_text = "\n".join(output_sections) if output_sections else None
        success = any_succeeded and not had_subproject_error and total_issues == 0
        return ToolResult(
            name=plugin.definition.name,
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


def _run_and_parse(
    plugin: TypeScriptCheckerPlugin,
    ctx: ExecutionContext,
    project_path: str | None,
    options: dict[str, object],
) -> ToolResult:
    """Build the checker command, run it, and parse the output.

    Args:
        plugin: TypeScript-checker plugin instance.
        ctx: Prepared execution context.
        project_path: ``--project`` path or ``None``.
        options: Merged runtime options.

    Returns:
        ToolResult with parsed issues.
    """
    cmd = plugin._build_command(
        files=ctx.rel_files if not project_path else [],
        project_path=project_path,
        options=options,
    )
    logger.debug(
        "[{}] Running with cwd={} and cmd={}",
        plugin._tool_label,
        ctx.cwd,
        cmd,
    )

    try:
        success, output = plugin._run_subprocess(
            cmd=cmd,
            timeout=ctx.timeout,
            cwd=ctx.cwd,
        )
    except subprocess.TimeoutExpired:
        timeout_result = create_timeout_result(
            tool=plugin,
            timeout=ctx.timeout,
            cmd=cmd,
        )
        return ToolResult(
            name=plugin.definition.name,
            success=timeout_result.success,
            output=timeout_result.output,
            issues_count=timeout_result.issues_count,
            issues=timeout_result.issues,
        )
    except FileNotFoundError as e:
        return ToolResult(
            name=plugin.definition.name,
            success=False,
            output=plugin._not_found_output(e),
            issues_count=0,
        )
    except OSError as e:
        logger.error(
            "[{}] Failed to run {}: {}",
            plugin._tool_label,
            plugin._tool_label,
            e,
        )
        return ToolResult(
            name=plugin.definition.name,
            success=False,
            output=f"{plugin._tool_label} execution failed: " + str(e),
            issues_count=0,
        )

    # Parse output (parser handles ANSI stripping internally)
    all_issues = plugin._parse_output(output or "")
    issues_count = len(all_issues)

    # Normalize output for fallback substring matching below
    normalized_output = strip_ansi_codes(output) if output else ""

    # Categorize issues into type errors vs dependency errors
    type_errors, dependency_errors = plugin._categorize_issues(all_issues)

    # If we have dependency errors, provide helpful guidance
    if dependency_errors:
        missing_modules = plugin._extract_missing_modules(dependency_errors)
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
            name=plugin.definition.name,
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
                name=plugin.definition.name,
                success=False,
                output=plugin._config_error_output(normalized_output),
                issues_count=0,
            )

        return ToolResult(
            name=plugin.definition.name,
            success=False,
            output=normalized_output or f"{plugin._tool_label} execution failed.",
            issues_count=0,
        )

    if not success and issues_count == 0:
        return ToolResult(
            name=plugin.definition.name,
            success=False,
            output=f"{plugin._tool_label} execution failed.",
            issues_count=0,
        )

    return ToolResult(
        name=plugin.definition.name,
        success=success and issues_count == 0,
        output=None,
        issues_count=issues_count,
        issues=all_issues,
    )
