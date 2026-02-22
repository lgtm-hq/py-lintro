"""Base implementation for Lintro plugins.

This module provides the BaseToolPlugin class, which implements common
functionality for all Lintro tool plugins.

Example:
    >>> from lintro.plugins.base import BaseToolPlugin
    >>> from lintro.plugins.protocol import ToolDefinition
    >>> from lintro.plugins.registry import register_tool
    >>>
    >>> @register_tool
    ... class MyPlugin(BaseToolPlugin):
    ...     @property
    ...     def definition(self) -> ToolDefinition:
    ...         return ToolDefinition(name="my-tool", description="My tool")
    ...
    ...     def check(self, paths, options):
    ...         # Implementation
    ...         pass
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import click
from loguru import logger

from lintro.config.lintro_config import LintroConfig
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.execution_preparation import (
    DEFAULT_TIMEOUT,
    build_config_args,
    get_defaults_config_args,
    get_effective_timeout,
    get_enforce_cli_args,
    get_enforced_settings,
    get_executable_command,
    get_lintro_config,
    prepare_execution,
    should_use_lintro_config,
    verify_tool_version,
)
from lintro.plugins.file_discovery import (
    DEFAULT_EXCLUDE_PATTERNS,
    discover_files,
    get_cwd,
    setup_exclude_patterns,
    validate_paths,
)
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.subprocess_executor import (
    run_subprocess,
    run_subprocess_streaming,
    validate_subprocess_command,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintro.plugins.file_processor import AggregatedResult, FileProcessingResult


@dataclass
class ExecutionContext:
    """Context for tool execution containing prepared files and metadata.

    This dataclass encapsulates the common preparation steps needed before
    running a tool, eliminating duplicate boilerplate across tool implementations.

    Attributes:
        files: List of absolute file paths to process.
        rel_files: List of file paths relative to cwd.
        cwd: Working directory for command execution.
        early_result: If set, return this result immediately.
        timeout: Timeout value for subprocess execution.
    """

    files: list[str] = field(default_factory=list)
    rel_files: list[str] = field(default_factory=list)
    cwd: str | None = None
    early_result: ToolResult | None = None
    timeout: int = DEFAULT_TIMEOUT

    @property
    def should_skip(self) -> bool:
        """Check if execution should be skipped due to early result.

        Returns:
            True if early_result is set and execution should be skipped.
        """
        return self.early_result is not None


@dataclass
class BaseToolPlugin(ABC):
    """Base class providing common functionality for tool plugins.

    This class implements the boilerplate that most tools need:
    - Subprocess execution with safety validation
    - File discovery and filtering
    - Version checking
    - Config injection support
    - Working directory computation

    Subclasses must implement:
    - definition property: Return a ToolDefinition with tool metadata
    - check() method: Check files for issues

    Optionally override:
    - fix() method: Fix issues (only if definition.can_fix=True)
    - set_options() method: Custom option validation

    Attributes:
        options: Current tool options (merged from defaults and runtime).
        exclude_patterns: Patterns to exclude from file discovery.
        include_venv: Whether to include virtual environment files.
    """

    options: dict[str, object] = field(default_factory=dict, init=False)
    exclude_patterns: list[str] = field(default_factory=list, init=False)
    include_venv: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Initialize plugin with defaults from definition."""
        # Initialize options from definition defaults
        self.options = dict(self.definition.default_options)

        # Set up exclude patterns
        self._setup_defaults()

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Must be implemented by subclasses.

        Returns:
            ToolDefinition containing all tool metadata.
        """
        ...

    @property
    def name(self) -> str:
        """Return the tool name from definition.

        Returns:
            str: Tool name.
        """
        return self.definition.name

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def set_options(self, **kwargs: object) -> None:
        """Set tool-specific options.

        Args:
            **kwargs: Tool-specific options.

        Raises:
            ValueError: If an option value is invalid.
        """
        from lintro.enums.tool_option_key import ToolOptionKey

        for key, value in kwargs.items():
            if key == ToolOptionKey.TIMEOUT.value:
                if value is not None and not isinstance(value, (int, float)):
                    raise ValueError("Timeout must be a number or None")
                kwargs[key] = float(value) if value is not None else None
            if key == ToolOptionKey.EXCLUDE_PATTERNS.value and not isinstance(
                value,
                list,
            ):
                raise ValueError("Exclude patterns must be a list")
            if key == ToolOptionKey.INCLUDE_VENV.value and not isinstance(value, bool):
                raise ValueError("Include venv must be a boolean")

        self.options.update(kwargs)

        # Update specific attributes — merge CLI patterns with existing
        # defaults and .lintro-ignore patterns instead of replacing them
        if ToolOptionKey.EXCLUDE_PATTERNS.value in kwargs:
            patterns = kwargs[ToolOptionKey.EXCLUDE_PATTERNS.value]
            if isinstance(patterns, list):
                seen = set(self.exclude_patterns)
                for p in patterns:
                    if p not in seen:
                        self.exclude_patterns.append(p)
                        seen.add(p)
        if ToolOptionKey.INCLUDE_VENV.value in kwargs:
            self.include_venv = bool(kwargs[ToolOptionKey.INCLUDE_VENV.value])

    def doc_url(self, code: str) -> str | None:
        """Return a documentation URL for the given rule code.

        Override in subclasses to provide tool-specific documentation links.

        Args:
            code: The rule/error code (e.g., "E501", "SC2086").

        Returns:
            Documentation URL string, or None if no docs are available.
        """
        return None

    @abstractmethod
    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files for issues.

        Args:
            paths: List of file or directory paths to check.
            options: Tool-specific options that override defaults.

        Returns:
            ToolResult containing check results and any issues found.
        """
        ...

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix issues in files.

        Default implementation raises NotImplementedError if can_fix=False.
        Override in subclasses that support fixing.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options that override defaults.

        Returns:
            ToolResult containing fix results and any remaining issues.

        Raises:
            NotImplementedError: If the tool doesn't support fixing.
        """
        if not self.definition.can_fix:
            raise NotImplementedError(
                f"{self.definition.name} does not support fixing issues",
            )
        raise NotImplementedError("Subclass must implement fix()")

    # -------------------------------------------------------------------------
    # Protected Methods - For use by subclasses
    # -------------------------------------------------------------------------

    def _setup_defaults(self) -> None:
        """Set up default options and patterns."""
        self.exclude_patterns = setup_exclude_patterns(self.exclude_patterns)

        # Set default timeout if not specified
        if "timeout" not in self.options:
            self.options["timeout"] = self.definition.default_timeout

    def _discover_files(
        self,
        paths: list[str],
        show_progress: bool = True,
    ) -> list[str]:
        """Discover files matching the tool's patterns.

        Args:
            paths: Input paths to search.
            show_progress: Whether to show a progress spinner during discovery.

        Returns:
            List of matching file paths.
        """
        return discover_files(
            paths=paths,
            definition=self.definition,
            exclude_patterns=self.exclude_patterns,
            include_venv=self.include_venv,
            show_progress=show_progress,
        )

    def _run_subprocess(
        self,
        cmd: list[str],
        timeout: int | float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[bool, str]:
        """Run a subprocess command safely.

        Args:
            cmd: Command and arguments to run.
            timeout: Timeout in seconds (defaults to tool's timeout).
            cwd: Working directory for command execution.
            env: Environment variables for the subprocess.

        Returns:
            Tuple of (success, output) where success indicates return code 0.
        """
        effective_timeout = self._get_effective_timeout(timeout)
        return run_subprocess(cmd, effective_timeout, cwd, env)

    def _run_subprocess_streaming(
        self,
        cmd: list[str],
        timeout: int | float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        line_handler: Callable[[str], None] | None = None,
    ) -> tuple[bool, str]:
        """Run a subprocess command with optional line-by-line streaming.

        This method allows real-time output processing by calling the line_handler
        callback for each line of output as it is produced by the subprocess.

        Args:
            cmd: Command and arguments to run.
            timeout: Timeout in seconds (defaults to tool's timeout).
            cwd: Working directory for command execution.
            env: Environment variables for the subprocess.
            line_handler: Optional callback called for each line of output.

        Returns:
            Tuple of (success, output) where success indicates return code 0.
        """
        effective_timeout = self._get_effective_timeout(timeout)
        return run_subprocess_streaming(cmd, effective_timeout, cwd, env, line_handler)

    def _get_effective_timeout(self, timeout: int | float | None = None) -> float:
        """Get the effective timeout value.

        Args:
            timeout: Override timeout value, or None to use default.

        Returns:
            Timeout value in seconds.
        """
        return get_effective_timeout(
            timeout,
            self.options,
            self.definition.default_timeout,
        )

    def _validate_subprocess_command(self, cmd: list[str]) -> None:
        """Validate a subprocess command for safety.

        Args:
            cmd: Command and arguments to validate.
        """
        validate_subprocess_command(cmd)

    def _validate_paths(self, paths: list[str]) -> None:
        """Validate that paths exist and are accessible.

        Args:
            paths: Paths to validate.
        """
        validate_paths(paths)

    def _get_cwd(self, paths: list[str]) -> str | None:
        """Get common parent directory for paths.

        Args:
            paths: Paths to compute common parent for.

        Returns:
            Common parent directory path, or None if not applicable.
        """
        return get_cwd(paths)

    def _prepare_execution(
        self,
        paths: list[str],
        options: dict[str, object],
        *,
        no_files_message: str = "No files to check.",
    ) -> ExecutionContext:
        """Prepare execution context with common boilerplate steps.

        This method consolidates repeated patterns:
        1. Merge options with defaults
        2. Validate input paths
        3. Discover files matching patterns (returns early if none found)
        4. Verify tool version requirements (skipped when no files match)
        5. Compute working directory and relative paths
        6. Calculate timeout based on provided options

        Args:
            paths: Input paths to process.
            options: Runtime options to merge with defaults.
            no_files_message: Message when no files are found.

        Returns:
            ExecutionContext with files, cwd, and optional early_result.

        Example:
            ctx = self._prepare_execution(paths, options)
            if ctx.should_skip:
                return ctx.early_result

            cmd = self._build_command(ctx.rel_files)
            success, output = self._run_subprocess(cmd, cwd=ctx.cwd)
        """
        logger.debug(f"[{self.name}] Preparing execution for {len(paths)} input paths")

        result = prepare_execution(
            paths=paths,
            options=options,
            definition=self.definition,
            exclude_patterns=self.exclude_patterns,
            include_venv=self.include_venv,
            current_options=self.options,
            no_files_message=no_files_message,
        )

        if "early_result" in result:
            early_result = result["early_result"]
            logger.debug(f"[{self.name}] Early exit: {early_result.output}")
            return ExecutionContext(early_result=early_result)

        files = result.get("files", [])
        timeout = result.get("timeout", DEFAULT_TIMEOUT)
        logger.debug(f"[{self.name}] Ready: {len(files)} files, timeout={timeout}s")

        return ExecutionContext(
            files=files,
            rel_files=result.get("rel_files", []),
            cwd=result.get("cwd"),
            timeout=timeout,
        )

    def _process_files_with_progress(
        self,
        files: list[str],
        processor: Callable[[str], FileProcessingResult],
        timeout: int,
        *,
        label: str = "Processing files",
        progress_threshold: int = 2,
    ) -> AggregatedResult:
        """Process files with optional progress bar.

        This method handles the common pattern of iterating through files,
        calling a processor function for each file, and aggregating results.
        It shows a progress bar when processing multiple files.

        Args:
            files: List of file paths to process.
            processor: Callable that processes a single file and returns
                FileProcessingResult. The processor should handle its own
                exceptions and return appropriate FileProcessingResult.
            timeout: Timeout for each file operation (included in output).
            label: Label for progress bar.
            progress_threshold: Minimum files to show progress bar.

        Returns:
            AggregatedResult with all file processing results.

        Example:
            def process_file(path: str) -> FileProcessingResult:
                try:
                    success, output = self._run_subprocess(cmd + [path])
                    issues = parse_output(output)
                    return FileProcessingResult(
                        success=success,
                        output=output,
                        issues=issues,
                    )
                except subprocess.TimeoutExpired:
                    return FileProcessingResult(
                        success=False,
                        output="",
                        issues=[],
                        skipped=True,
                    )

            result = self._process_files_with_progress(
                files=ctx.files,
                processor=process_file,
                timeout=ctx.timeout,
            )
        """
        from lintro.plugins.file_processor import AggregatedResult

        aggregated = AggregatedResult()

        if len(files) >= progress_threshold:
            with click.progressbar(
                files,
                label=label,
                bar_template="%(label)s  %(info)s",
            ) as bar:
                for file_path in bar:
                    result = processor(file_path)
                    aggregated.add_file_result(file_path, result)
        else:
            for file_path in files:
                result = processor(file_path)
                aggregated.add_file_result(file_path, result)

        return aggregated

    def _get_executable_command(self, tool_name: str) -> list[str]:
        """Get the command prefix to execute a tool.

        Delegates to CommandBuilderRegistry for language-specific logic.
        This satisfies ISP by keeping BaseToolPlugin language-agnostic.

        Args:
            tool_name: Name of the tool executable.

        Returns:
            Command prefix list.
        """
        return get_executable_command(tool_name)

    def _verify_tool_version(self) -> ToolResult | None:
        """Verify that the tool meets minimum version requirements.

        Returns:
            None if version check passes, or a skip result if it fails.
        """
        return verify_tool_version(self.definition)

    # -------------------------------------------------------------------------
    # Lintro Config Support
    # -------------------------------------------------------------------------

    def _get_lintro_config(self) -> LintroConfig:
        """Get the current Lintro configuration.

        Returns:
            The current LintroConfig instance.
        """
        return get_lintro_config()

    def _get_enforced_settings(self) -> dict[str, object]:
        """Get enforced settings as a dictionary.

        Returns:
            Dictionary of enforced settings.
        """
        return get_enforced_settings(lintro_config=self._get_lintro_config())

    def _get_enforce_cli_args(self) -> list[str]:
        """Get CLI arguments for enforced settings.

        Returns:
            List of CLI arguments for enforced settings.
        """
        return get_enforce_cli_args(
            tool_name=self.definition.name,
            lintro_config=self._get_lintro_config(),
        )

    def _get_defaults_config_args(self) -> list[str]:
        """Get CLI arguments for defaults config injection.

        Returns:
            List of CLI arguments for defaults config.
        """
        return get_defaults_config_args(
            tool_name=self.definition.name,
            lintro_config=self._get_lintro_config(),
        )

    def _should_use_lintro_config(self) -> bool:
        """Check if Lintro config should be used for this tool.

        Returns:
            True if Lintro config should be used.
        """
        return should_use_lintro_config(tool_name=self.definition.name)

    def _build_config_args(self) -> list[str]:
        """Build combined CLI arguments for config injection.

        Returns:
            List of combined CLI arguments for config.
        """
        return build_config_args(
            tool_name=self.definition.name,
            lintro_config=self._get_lintro_config(),
        )


__all__ = [
    "DEFAULT_EXCLUDE_PATTERNS",
    "DEFAULT_TIMEOUT",
    "BaseToolPlugin",
    "ExecutionContext",
]
