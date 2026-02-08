"""Mypy tool definition.

Mypy is a static type checker for Python that helps catch type-related
bugs before runtime. It uses type annotations (PEP 484) to verify that
your code is type-safe.
"""

from __future__ import annotations

import fnmatch
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.mypy.mypy_parser import parse_mypy_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.timeout_utils import create_timeout_result
from lintro.utils.config import load_mypy_config

# Constants for Mypy configuration
MYPY_DEFAULT_TIMEOUT: int = 60
MYPY_DEFAULT_PRIORITY: int = 82
MYPY_FILE_PATTERNS: list[str] = ["*.py", "*.pyi"]

MYPY_DEFAULT_EXCLUDE_PATTERNS: list[str] = [
    "test_samples/*",
    "test_samples/**",
    "*/test_samples/*",
    "*/test_samples/**",
    "node_modules/**",
    "dist/**",
    "build/**",
]


def _split_config_values(raw_value: str) -> list[str]:
    """Split config strings that may be comma or newline separated.

    Args:
        raw_value: Raw string from configuration that may contain commas or
            newlines.

    Returns:
        list[str]: Individual, stripped config entries.
    """
    entries: list[str] = []
    for part in raw_value.replace("\n", ",").split(","):
        value = part.strip()
        if value:
            entries.append(value)
    return entries


def _regex_to_glob(pattern: str) -> str:
    """Coerce a simple regex pattern to a fnmatch glob.

    Args:
        pattern: Regex-style pattern to coerce.

    Returns:
        str: A best-effort fnmatch-style glob pattern.
    """
    cleaned = pattern.strip()
    if cleaned.startswith("^"):
        cleaned = cleaned[1:]
    if cleaned.endswith("$"):
        cleaned = cleaned[:-1]
    cleaned = cleaned.replace(".*", "*")
    if cleaned.endswith("/"):
        cleaned = f"{cleaned}**"
    return cleaned


@register_tool
@dataclass
class MypyPlugin(BaseToolPlugin):
    """Mypy static type checker plugin.

    This plugin integrates Mypy with Lintro for static type checking
    of Python files.
    """

    # Internal state for config
    _config_data: dict[str, Any] | None = None
    _config_path: Path | None = None

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="mypy",
            description="Static type checker for Python",
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.TYPE_CHECKER,
            file_patterns=MYPY_FILE_PATTERNS,
            priority=MYPY_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["mypy.ini", ".mypy.ini", "pyproject.toml", "setup.cfg"],
            version_command=["mypy", "--version"],
            min_version="1.0.0",
            default_options={
                "timeout": MYPY_DEFAULT_TIMEOUT,
                "strict": True,
                "ignore_missing_imports": True,
                "python_version": None,
                "config_file": None,
                "cache_dir": None,
            },
            default_timeout=MYPY_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        strict: bool | None = None,
        ignore_missing_imports: bool | None = None,
        python_version: str | None = None,
        config_file: str | None = None,
        cache_dir: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Mypy-specific options.

        Args:
            strict: Enable strict mode for more rigorous type checking.
            ignore_missing_imports: Ignore missing imports.
            python_version: Python version target (e.g., "3.10").
            config_file: Path to mypy config file.
            cache_dir: Path to mypy cache directory.
            **kwargs: Other tool options.

        Raises:
            ValueError: If any provided option is of an unexpected type.
        """
        if strict is not None and not isinstance(strict, bool):
            raise ValueError("strict must be a boolean")
        if ignore_missing_imports is not None and not isinstance(
            ignore_missing_imports,
            bool,
        ):
            raise ValueError("ignore_missing_imports must be a boolean")
        if python_version is not None and not isinstance(python_version, str):
            raise ValueError("python_version must be a string")
        if config_file is not None and not isinstance(config_file, str):
            raise ValueError("config_file must be a string path")
        if cache_dir is not None and not isinstance(cache_dir, str):
            raise ValueError("cache_dir must be a string path")

        options: dict[str, object] = {
            "strict": strict,
            "ignore_missing_imports": ignore_missing_imports,
            "python_version": python_version,
            "config_file": config_file,
            "cache_dir": cache_dir,
        }
        options = {k: v for k, v in options.items() if v is not None}
        super().set_options(**options, **kwargs)

    def _glob_to_regex(self, pattern: str) -> str:
        """Convert a glob pattern to a mypy --exclude regex pattern.

        Args:
            pattern: Glob-style pattern (e.g., "test_samples/**", "*.pyc").

        Returns:
            Regex pattern suitable for mypy --exclude.
        """
        # Convert glob to regex and escape special chars except glob wildcards
        regex = fnmatch.translate(pattern)
        # Remove anchors added by fnmatch.translate (\\Z or \\z at end, ^ at start)
        # Python 3.14+ changed fnmatch.translate to use \\z instead of \\Z
        if regex.endswith("\\Z") or regex.endswith("\\z"):
            regex = regex[:-2]
        if regex.endswith("$"):
            regex = regex[:-1]
        if regex.startswith("^"):
            regex = regex[1:]
        if regex.startswith("(?s:"):
            regex = regex[4:]
        if regex.endswith(")"):
            regex = regex[:-1]

        # Anchor directory segments to path boundaries to avoid matching substrings
        # For patterns starting with a literal directory (e.g., "dist/**"), prefix
        # with (?:^|/) so it matches only whole directory components
        if pattern and not pattern.startswith("*"):
            # Pattern starts with a literal - anchor to path boundary
            regex = f"(?:^|/){regex}"

        return regex

    def _build_command(
        self,
        files: list[str],
        *,
        excludes: list[str] | None = None,
        exclude_regexes: list[str] | None = None,
    ) -> list[str]:
        """Build the mypy invocation command.

        Args:
            files: Relative file paths that should be checked by mypy.
            excludes: Optional list of glob patterns to convert to --exclude args.
            exclude_regexes: Optional list of raw regex patterns to pass directly
                to --exclude (preserves original config patterns without conversion).

        Returns:
            A list of command arguments ready to be executed.
        """
        cmd: list[str] = self._get_executable_command(tool_name="mypy")
        config_args = self._build_config_args()
        enforced = self._get_enforced_settings()
        cmd.extend(
            [
                "--output",
                "json",
                "--show-error-codes",
                "--show-column-numbers",
                "--hide-error-context",
                "--no-error-summary",
                "--explicit-package-bases",
            ],
        )

        if config_args:
            cmd.extend(config_args)

        if self.options.get("strict") is True:
            cmd.append("--strict")
        if self.options.get("ignore_missing_imports", True):
            cmd.append("--ignore-missing-imports")

        if self.options.get("python_version") and "target_python" not in enforced:
            cmd.extend(["--python-version", str(self.options["python_version"])])
        if self.options.get("config_file") and "--config-file" not in config_args:
            cmd.extend(["--config-file", str(self.options["config_file"])])
        if self.options.get("cache_dir"):
            cmd.extend(["--cache-dir", str(self.options["cache_dir"])])

        # Add raw regex excludes first (from config, preserves fidelity)
        if exclude_regexes:
            for regex in exclude_regexes:
                stripped = regex.strip()
                if stripped:
                    cmd.extend(["--exclude", stripped])

        # Add glob-based excludes (converted to regex for lintro defaults)
        if excludes:
            for pattern in excludes:
                regex = self._glob_to_regex(pattern)
                if regex:
                    cmd.extend(["--exclude", regex])

        cmd.extend(files)
        return cmd

    def _build_effective_excludes(self, configured_excludes: Any) -> list[str]:
        """Build effective exclude patterns from config and defaults.

        Always includes default patterns, then adds any configured excludes.
        This ensures common directories (tests/, build/, dist/) are always
        excluded unless explicitly overridden.

        Args:
            configured_excludes: Exclude patterns from mypy config.

        Returns:
            list[str]: Combined exclude patterns.
        """
        effective_excludes: list[str] = list(self.exclude_patterns)

        # Always add default patterns first
        for default_pattern in MYPY_DEFAULT_EXCLUDE_PATTERNS:
            if default_pattern not in effective_excludes:
                effective_excludes.append(default_pattern)

        # Then add configured excludes (if any)
        if configured_excludes:
            raw_excludes = (
                [configured_excludes]
                if isinstance(configured_excludes, str)
                else list(configured_excludes)
            )
            for pattern in raw_excludes:
                glob_pattern = _regex_to_glob(str(pattern))
                if glob_pattern and glob_pattern not in effective_excludes:
                    effective_excludes.append(glob_pattern)

        return effective_excludes

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Mypy.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Load mypy config first (needed to determine paths and excludes)
        base_dir = Path.cwd()
        config_data, config_path = load_mypy_config(base_dir=base_dir)
        self._config_data, self._config_path = config_data, config_path
        if config_path:
            logger.debug("Discovered mypy config at {}", config_path)

        # Determine target paths (use config files if paths empty)
        target_paths: list[str] = list(paths) if paths else []
        configured_files = config_data.get("files")
        if (not target_paths or target_paths == ["."]) and configured_files:
            if isinstance(configured_files, str):
                target_paths = [configured_files]
            elif isinstance(configured_files, list):
                target_paths = [
                    str(path) for path in configured_files if str(path).strip()
                ]

        # Build effective excludes from config
        configured_excludes = config_data.get("exclude")
        effective_excludes = self._build_effective_excludes(configured_excludes)
        logger.debug("Effective mypy exclude patterns: {}", effective_excludes)

        # Preserve raw config regexes for direct passing to mypy CLI
        configured_exclude_regexes: list[str] = []
        if configured_excludes:
            raw_excludes = (
                [configured_excludes]
                if isinstance(configured_excludes, str)
                else list(configured_excludes)
            )
            configured_exclude_regexes = [
                str(x).strip() for x in raw_excludes if str(x).strip()
            ]

        # Temporarily update exclude patterns for file discovery
        original_excludes = self.exclude_patterns
        self.exclude_patterns = effective_excludes

        # Use shared preparation with custom excludes
        ctx = self._prepare_execution(
            target_paths,
            merged_options,
            no_files_message="No files to check.",
        )

        # Restore original exclude patterns
        self.exclude_patterns = original_excludes

        if ctx.should_skip and ctx.early_result is not None:
            return ctx.early_result

        # Safety check: if should_skip but no early_result, create one
        if ctx.should_skip:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No files to check.",
                issues_count=0,
            )

        logger.debug("[mypy] Discovered {} python file(s)", len(ctx.files))

        # Set config file if discovered
        if not self.options.get("config_file") and config_path:
            self.options["config_file"] = str(config_path.resolve())
            logger.debug(
                "Setting mypy --config-file to {}",
                self.options["config_file"],
            )

        # Mypy needs to run from the project root to properly resolve module
        # structure. When checking a package directory, passing individual files
        # causes "Duplicate module named __main__" and type resolution errors.
        # Instead, we run from the original directory and pass the target paths.
        use_project_root = any(Path(p).is_dir() for p in target_paths)

        effective_cwd: str | None
        mypy_excludes: list[str] | None = None
        mypy_exclude_regexes: list[str] | None = None
        if use_project_root:
            # Run mypy from where user invoked lintro, pass original paths
            # Since we're passing directories, we need to pass excludes to mypy
            # so it respects lintro's file filtering
            effective_cwd = str(Path.cwd())
            mypy_targets = target_paths
            # Pass raw config regexes directly (preserves fidelity)
            # Only pass lintro defaults as globs (not config-derived ones)
            mypy_exclude_regexes = configured_exclude_regexes or None
            # Deduplicate excludes while preserving order
            mypy_excludes = list(
                dict.fromkeys(
                    list(self.exclude_patterns) + list(MYPY_DEFAULT_EXCLUDE_PATTERNS),
                ),
            )
        else:
            # For individual files, use the computed cwd and relative paths
            effective_cwd = ctx.cwd
            mypy_targets = ctx.rel_files if ctx.rel_files else target_paths

        cmd = self._build_command(
            files=mypy_targets,
            excludes=mypy_excludes,
            exclude_regexes=mypy_exclude_regexes,
        )
        logger.debug("[mypy] Running with cwd={} and cmd={}", effective_cwd, cmd)

        try:
            success, output = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=effective_cwd,
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
                output=f"mypy not found: {e}\n\n"
                "Please ensure mypy is installed:\n"
                "  - Run 'pip install mypy' or 'uv pip install mypy'",
                issues_count=0,
            )
        except (OSError, ValueError, RuntimeError) as e:
            logger.error("Failed to run mypy: {}", e)
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"mypy execution failed: {e}",
                issues_count=0,
            )

        issues = parse_mypy_output(output=output)
        issues_count = len(issues)

        if not success and issues_count == 0:
            # Execution failed but no structured issues were parsed; surface raw output
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=output or "mypy execution failed.",
                issues_count=0,
            )

        return ToolResult(
            name=self.definition.name,
            success=issues_count == 0,
            output=None,
            issues_count=issues_count,
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Mypy does not support auto-fixing.

        Args:
            paths: Paths or files passed for completeness.
            options: Runtime options (unused).

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Always, because mypy cannot fix issues.
        """
        raise NotImplementedError(
            "Mypy cannot automatically fix issues. Run 'lintro check' to see "
            "type errors that need manual correction.",
        )
