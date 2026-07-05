"""Pytest tool definition.

Pytest is a mature full-featured Python testing tool that helps you write
better programs. It supports various testing patterns, fixtures, parametrization,
and provides extensive plugin support for customization.
"""

from __future__ import annotations

import copy
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.implementations.pytest.output import (
    load_file_patterns_from_config,
    load_pytest_config,
)
from lintro.tools.implementations.pytest.pytest_command_builder import (
    build_check_command,
)
from lintro.tools.implementations.pytest.pytest_config import PytestConfiguration
from lintro.tools.implementations.pytest.pytest_error_handler import PytestErrorHandler
from lintro.tools.implementations.pytest.pytest_executor import PytestExecutor
from lintro.tools.implementations.pytest.pytest_output_processor import (
    parse_pytest_output_with_fallback,
)
from lintro.tools.implementations.pytest.pytest_result_processor import (
    PytestResultProcessor,
)
from lintro.utils.path_utils import load_lintro_ignore

# Constants for pytest configuration
PYTEST_DEFAULT_TIMEOUT: int = 300  # 5 minutes for test runs
PYTEST_DEFAULT_PRIORITY: int = 90
PYTEST_FILE_PATTERNS: list[str] = ["test_*.py", "*_test.py"]


@register_tool
@dataclass
class PytestPlugin(BaseToolPlugin):
    """Pytest test runner plugin.

    This plugin integrates Pytest with Lintro for running Python tests
    and collecting test results.

    Attributes:
        pytest_config: Pytest-specific configuration.
        executor: Test execution handler.
        result_processor: Result processing handler.
        error_handler: Error handling handler.
    """

    # Pytest-specific components
    pytest_config: PytestConfiguration = field(default_factory=PytestConfiguration)
    executor: PytestExecutor | None = field(default=None, init=False)
    result_processor: PytestResultProcessor | None = field(default=None, init=False)
    error_handler: PytestErrorHandler | None = field(default=None, init=False)

    # Internal storage for file patterns from config
    _file_patterns_from_config: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        """Initialize plugin with pytest-specific components."""
        super().__post_init__()

        # Load lintro ignore patterns
        ignore_patterns = load_lintro_ignore()
        for pattern in ignore_patterns:
            if pattern not in self.exclude_patterns:
                self.exclude_patterns.append(pattern)

        # Load pytest configuration and file patterns
        pytest_config = load_pytest_config()
        config_file_patterns = load_file_patterns_from_config(pytest_config)
        if config_file_patterns:
            self._file_patterns_from_config = config_file_patterns

        # Apply any additional config options from pytest_config
        if pytest_config and "options" in pytest_config:
            self.options.update(pytest_config.get("options", {}))

        # Initialize the components with tool reference
        self.executor = PytestExecutor(
            config=self.pytest_config,
            tool=self,
        )
        self.result_processor = PytestResultProcessor(
            self.pytest_config,
            self.definition.name,
        )
        self.error_handler = PytestErrorHandler(self.definition.name)

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="pytest",
            description="Mature full-featured Python testing tool",
            can_fix=False,
            tool_type=ToolType.TEST_RUNNER,
            file_patterns=PYTEST_FILE_PATTERNS,
            priority=PYTEST_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[
                "pytest.ini",
                "pyproject.toml",
                "setup.cfg",
                "tox.ini",
                "conftest.py",
            ],
            version_command=["pytest", "--version"],
            min_version=get_min_version(ToolName.PYTEST),
            default_options={
                "timeout": PYTEST_DEFAULT_TIMEOUT,
                "verbose": False,
                "capture": None,
                "markers": None,
                "keywords": None,
                "maxfail": None,
                "exitfirst": False,
                "last_failed": False,
                "collect_only": False,
            },
            default_timeout=PYTEST_DEFAULT_TIMEOUT,
        )

    def set_options(self, **kwargs: Any) -> None:
        """Set pytest-specific options.

        Args:
            **kwargs: Option key-value pairs to set.

        Delegates to PytestConfiguration for option management and validation.
        Forwards unrecognized options (like timeout) to the base class.
        """
        # Extract pytest-specific options
        config_fields = {
            f.name for f in self.pytest_config.__dataclass_fields__.values()
        }
        pytest_options = {k: v for k, v in kwargs.items() if k in config_fields}
        base_options = {k: v for k, v in kwargs.items() if k not in config_fields}

        # Set pytest-specific options
        self.pytest_config.set_options(**pytest_options)

        # Forward unrecognized options (like timeout) to base class
        if base_options:
            super().set_options(**base_options)

        # Set pytest options on the parent class (for backward compatibility)
        options_dict = self.pytest_config.get_options_dict()
        super().set_options(**options_dict)

    def _isolate_execution_state(self) -> None:
        """Isolate pytest-specific mutable option state on this copy.

        ``pytest_config`` is a mutable dataclass that :meth:`set_options`
        mutates. The shallow copy produced by
        :meth:`~lintro.plugins.base.BaseToolPlugin.copy_for_execution` leaves
        it (and the collaborators that reference it) shared with the registry
        singleton, so concurrent invocations would race on it. Replace it with
        an independent deep copy and re-wire the executor and result processor
        that hold references to it (and to this plugin).
        """
        super()._isolate_execution_state()

        self.pytest_config = copy.deepcopy(self.pytest_config)

        if self.executor is not None:
            self.executor = copy.copy(self.executor)
            self.executor.config = self.pytest_config
            self.executor.tool = self
        if self.result_processor is not None:
            self.result_processor = copy.copy(self.result_processor)
            self.result_processor.config = self.pytest_config
        # error_handler holds only the immutable tool name; safe to share.

    def _reset_execution_state(self) -> None:
        """Reset pytest-specific mutable option state back to defaults.

        ``reset_options`` restores the base option attributes, but the mutable
        ``pytest_config`` dataclass is owned by this subclass. Without this
        override a value mutated on the template (e.g. ``maxfail`` or
        ``collect_only``) would survive into a freshly configured execution
        copy, so pytest could stop early or only collect tests even though the
        caller never requested it. Replace the config with a default instance
        and re-point the collaborators that reference it.
        """
        super()._reset_execution_state()

        self.pytest_config = PytestConfiguration()

        if self.executor is not None:
            self.executor.config = self.pytest_config
        if self.result_processor is not None:
            self.result_processor.config = self.pytest_config
        # error_handler holds only the immutable tool name; safe to share.

    def _parse_output(
        self,
        output: str,
        return_code: int,
        junitxml_path: str | None = None,
        subprocess_start_time: float | None = None,
    ) -> list[Any]:
        """Parse pytest output into issues.

        Args:
            output: Raw output from pytest.
            return_code: Return code from pytest.
            junitxml_path: Optional path to JUnit XML file (from auto_junitxml).
            subprocess_start_time: Optional Unix timestamp when subprocess started.

        Returns:
            list: Parsed test failures and errors.
        """
        # Build options dict for parser
        options = self.options.copy() if junitxml_path else self.options
        if junitxml_path:
            options["junitxml"] = junitxml_path

        return parse_pytest_output_with_fallback(
            output=output,
            return_code=return_code,
            options=options,
            subprocess_start_time=subprocess_start_time,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run pytest on specified files.

        Args:
            paths: List of file or directory paths to test.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Results from pytest execution.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Check version requirements
        version_result = self._verify_tool_version()
        if version_result is not None:
            return version_result

        # For pytest, when no specific files are provided, use directories to let
        # pytest discover all tests. This allows running all tests by default.
        target_files = paths
        if target_files is None or not target_files:
            # Default to "tests" directory to match pytest conventions
            target_files = ["tests"]
        elif (
            isinstance(target_files, list)
            and len(target_files) == 1
            and target_files[0] == "."
        ):
            # If just "." is provided, also default to "tests" directory
            target_files = ["tests"]

        # Handle special modes first (these don't run tests)
        from lintro.enums.pytest_enums import PytestSpecialMode
        from lintro.tools.implementations.pytest.pytest_handlers import (
            handle_check_plugins,
            handle_collect_only,
            handle_fixture_info,
            handle_list_fixtures,
            handle_list_markers,
            handle_list_plugins,
            handle_parametrize_help,
        )

        special_mode = self.pytest_config.get_special_mode()
        if special_mode:
            mode_value = self.pytest_config.get_special_mode_value(special_mode)

            if special_mode == PytestSpecialMode.LIST_PLUGINS:
                return handle_list_plugins(self)
            elif special_mode == PytestSpecialMode.CHECK_PLUGINS:
                return handle_check_plugins(self, mode_value)
            elif special_mode == PytestSpecialMode.COLLECT_ONLY:
                return handle_collect_only(self, target_files)
            elif special_mode == PytestSpecialMode.LIST_FIXTURES:
                return handle_list_fixtures(self, target_files)
            elif special_mode == PytestSpecialMode.FIXTURE_INFO:
                return handle_fixture_info(self, mode_value, target_files)
            elif special_mode == PytestSpecialMode.LIST_MARKERS:
                return handle_list_markers(self)
            elif special_mode == PytestSpecialMode.PARAMETRIZE_HELP:
                return handle_parametrize_help(self)

        # Normal test execution
        cmd, auto_junitxml_path = build_check_command(self, target_files, fix=False)

        logger.debug(f"Running pytest with command: {' '.join(cmd)}")
        logger.debug(f"Target files: {target_files}")

        # Prepare test execution using executor
        if self.executor is None:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output="Pytest executor not initialized",
                issues_count=0,
            )

        total_available_tests = self.executor.prepare_test_execution(target_files)

        # Display run configuration summary
        self.executor.display_run_config(total_available_tests, target_files)

        try:
            # Record start time to filter out stale junitxml files
            import time

            subprocess_start_time = time.time()

            # Execute tests using executor
            success, output, return_code = self.executor.execute_tests(cmd)

            # Parse output
            issues = self._parse_output(
                output,
                return_code,
                auto_junitxml_path,
                subprocess_start_time,
            )

            # Process results using result processor
            if self.result_processor is None:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output="Pytest result processor not initialized",
                    issues_count=0,
                )

            summary_data, all_issues = self.result_processor.process_test_results(
                output=output,
                return_code=return_code,
                issues=issues,
                total_available_tests=total_available_tests,
            )

            # Build result using result processor
            return self.result_processor.build_result(
                success,
                summary_data,
                all_issues,
                raw_output=output,
            )

        except subprocess.TimeoutExpired:
            timeout_opt = self.options.get("timeout", PYTEST_DEFAULT_TIMEOUT)
            if isinstance(timeout_opt, int):
                timeout_val = timeout_opt
            elif timeout_opt is not None:
                timeout_val = int(str(timeout_opt))
            else:
                timeout_val = PYTEST_DEFAULT_TIMEOUT

            if self.error_handler is None:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=f"Pytest execution timed out ({timeout_val}s)",
                    issues_count=0,
                )

            return self.error_handler.handle_timeout_error(
                timeout_val,
                cmd,
                initial_count=0,
            )
        except (OSError, ValueError, RuntimeError) as e:
            if self.error_handler is None:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=f"Pytest execution failed: {e}",
                    issues_count=0,
                )
            return self.error_handler.handle_execution_error(e, cmd)

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Pytest does not support fixing issues.

        Args:
            paths: List of file paths (unused).
            options: Runtime options (unused).

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: pytest does not support fixing issues.
        """
        raise NotImplementedError(
            "Pytest cannot automatically fix issues. It only runs tests - "
            "fix test failures by modifying your code or tests directly.",
        )
