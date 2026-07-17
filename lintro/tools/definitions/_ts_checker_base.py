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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, NoReturn

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin, ExecutionContext
from lintro.tools.core.option_validators import (
    OptionSchema,
    validate_option_types,
)
from lintro.tools.definitions import _ts_checker_command as ts_cmd
from lintro.tools.definitions import _ts_checker_execution as ts_exec

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
    small set of hook methods declared at the bottom of this class. Shared
    command construction and check orchestration live in sibling modules.
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
        return ts_cmd._resolve_binary_command(
            binary=binary,
        )

    def _find_tsconfig(self, cwd: Path) -> Path | None:
        """Find a tsconfig for the working directory or explicit project option.

        Args:
            cwd: Working directory to search for a tsconfig.

        Returns:
            Path to the tsconfig if found, None otherwise.
        """
        return ts_cmd._find_tsconfig(
            plugin=self,
            cwd=cwd,
        )

    def _preferred_candidate_tsconfig(self, discovery_root: Path) -> Path | None:
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
            discovery_root: Directory scanned for a preferred tsconfig.

        Returns:
            Path to the preferred tsconfig if one exists, otherwise ``None``.
        """
        return ts_cmd._preferred_candidate_tsconfig(
            plugin=self,
            discovery_root=discovery_root,
        )

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
        return ts_cmd._create_temp_tsconfig(
            plugin=self,
            base_tsconfig=base_tsconfig,
            files=files,
            cwd=cwd,
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
        return ts_cmd._build_command(
            plugin=self,
            files=files,
            project_path=project_path,
            options=options,
        )

    def doc_url(self, code: str) -> str | None:
        """Return TypeScript error documentation URL.

        Uses typescript.tv, a third-party error reference, since the
        official TypeScript handbook does not provide per-error pages.

        Args:
            code: TypeScript error code (e.g., "TS2307" or "2307").

        Returns:
            URL to the TypeScript error documentation, or None if invalid.
        """
        return ts_cmd.doc_url(
            code=code,
        )

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
        return ts_exec.check(
            plugin=self,
            paths=paths,
            options=options,
        )

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
        return ts_exec._check_single_project(
            plugin=self,
            ctx=ctx,
            cwd_path=cwd_path,
            options=options,
            use_project_files=use_project_files,
            explicit_project=explicit_project,
            discovered_tsconfig=discovered_tsconfig,
        )

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
        return ts_exec._check_multi_project(
            plugin=self,
            ctx=ctx,
            cwd_path=cwd_path,
            tsconfigs=tsconfigs,
            options=options,
        )

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
        return ts_exec._run_and_parse(
            plugin=self,
            ctx=ctx,
            project_path=project_path,
            options=options,
        )

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
