"""Command and tsconfig helpers for TypeScript-checker plugins."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.utils.tsconfig import create_temp_tsconfig

if TYPE_CHECKING:
    from lintro.tools.definitions._ts_checker_base import TypeScriptCheckerPlugin


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


def _find_tsconfig(plugin: TypeScriptCheckerPlugin, cwd: Path) -> Path | None:
    """Find a tsconfig for the working directory or explicit project option.

    Args:
        plugin: TypeScript-checker plugin instance.
        cwd: Working directory to search for a tsconfig.

    Returns:
        Path to the tsconfig if found, None otherwise.
    """
    # Check explicit project option first
    project_opt = plugin.options.get("project")
    if project_opt and isinstance(project_opt, str):
        project_path = Path(project_opt)
        if project_path.is_absolute():
            return project_path if project_path.exists() else None
        resolved = cwd / project_path
        return resolved if resolved.exists() else None

    # Probe candidate tsconfig filenames in priority order
    for candidate in plugin._tsconfig_candidates:
        tsconfig = cwd / candidate
        if tsconfig.exists():
            return tsconfig
    return None


def _preferred_candidate_tsconfig(
    plugin: TypeScriptCheckerPlugin,
    discovery_root: Path,
) -> Path | None:
    """Find a subclass-preferred tsconfig ahead of generic discovery.

    Iterates ``_tsconfig_candidates`` in declared order and returns the
    first candidate that exists directly in *discovery_root* and is listed
    ahead of the generic ``tsconfig.json`` default. This lets a subclass
    such as :class:`~lintro.tools.definitions.vue_tsc.VueTscPlugin` — which
    prefers ``tsconfig.app.json`` for Vite Vue projects — win over generic
    multi-project discovery on the ``check()`` path (issue #1112).

    Candidates from ``tsconfig.json`` onward are intentionally ignored so
    that generic discovery (``references``, monorepo directory walking)
    stays in charge of the default config. Tools whose only candidate is
    ``tsconfig.json`` (e.g. ``tsc``) therefore never short-circuit here,
    keeping their behavior unchanged.

    Args:
        plugin: TypeScript-checker plugin instance.
        discovery_root: Directory scanned for a preferred tsconfig.

    Returns:
        Path to the preferred tsconfig if one exists, otherwise ``None``.
    """
    for candidate in plugin._tsconfig_candidates:
        if candidate == "tsconfig.json":
            break
        candidate_path = discovery_root / candidate
        if candidate_path.exists():
            return candidate_path.resolve()
    return None


def _create_temp_tsconfig(
    plugin: TypeScriptCheckerPlugin,
    base_tsconfig: Path,
    files: list[str],
    cwd: Path,
) -> Path:
    """Create a temporary tsconfig.json that extends the base config.

    Delegates to the shared implementation in
    :func:`lintro.utils.tsconfig.create_temp_tsconfig`.

    Args:
        plugin: TypeScript-checker plugin instance.
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
        prefix=plugin._temp_config_prefix,
        tool_label=plugin._tool_label,
    )


def _build_command(
    plugin: TypeScriptCheckerPlugin,
    files: list[str],
    project_path: str | Path | None = None,
    options: dict[str, object] | None = None,
) -> list[str]:
    """Build the checker invocation command.

    Args:
        plugin: TypeScript-checker plugin instance.
        files: Relative file paths (used only when no project config).
        project_path: Path to tsconfig.json to use (temp or user-specified).
        options: Options dict to use for flags. Defaults to plugin.options.

    Returns:
        A list of command arguments ready to be executed.
    """
    if options is None:
        options = plugin.options

    cmd: list[str] = list(plugin._command_prefix())

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


def doc_url(plugin: TypeScriptCheckerPlugin, code: str) -> str | None:
    """Return TypeScript error documentation URL.

    Uses typescript.tv, a third-party error reference, since the
    official TypeScript handbook does not provide per-error pages.

    Args:
        plugin: TypeScript-checker plugin instance.
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
