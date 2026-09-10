"""Pytest configuration management.

This module contains the PytestConfiguration dataclass that encapsulates
all pytest-specific option management and validation logic.
"""

from dataclasses import dataclass, field
from typing import Any

from lintro.enums.pytest_enums import PytestSpecialMode
from lintro.tools.pytest.pytest_option_validators import (
    validate_pytest_options,
)


@dataclass
class PytestConfiguration:
    """Configuration class for pytest-specific options.

    This dataclass encapsulates all pytest configuration options and provides
    validation and management methods. It follows the project's preference for
    dataclasses and proper data modeling.

    Attributes:
        verbose: Enable verbose output.
        tb: Traceback format (short, long, auto, line, native).
        maxfail: Stop after first N failures.
        no_header: Disable header.
        disable_warnings: Disable warnings.
        json_report: Enable JSON report output.
        junitxml: Path for JUnit XML output.
        slow_test_threshold: Duration threshold in seconds for slow test warning
            (default: 1.0).
        total_time_warning: Total execution time threshold in seconds for warning
            (default: 60.0).
        workers: Number of parallel workers for pytest-xdist (auto, N, or None).
        coverage_threshold: Minimum coverage percentage to require (0-100).
        auto_junitxml: Auto-enable junitxml in CI environments (default: True).
        detect_flaky: Enable flaky test detection (default: True).
        flaky_min_runs: Minimum runs before detecting flaky tests (default: 3).
        flaky_failure_rate: Minimum failure rate to consider flaky (default: 0.3).
        html_report: Path for HTML report output (pytest-html plugin).
        parallel_preset: Parallel execution preset (auto, small, medium, large).
        list_plugins: List all installed pytest plugins.
        check_plugins: Check if required plugins are installed.
        required_plugins: Comma-separated list of required plugin names.
        coverage_html: Path for HTML coverage report (requires pytest-cov).
        coverage_xml: Path for XML coverage report (requires pytest-cov).
        coverage_report: Generate both HTML and XML coverage reports.
        coverage_term_missing: Show coverage report in terminal with missing lines.
        collect_only: List tests without executing them.
        list_fixtures: List all available fixtures.
        fixture_info: Show detailed information about a specific fixture.
        list_markers: List all available markers.
        parametrize_help: Show help for parametrized tests.
        show_progress: Show progress during test execution (default: True).
        timeout: Timeout in seconds for individual tests (pytest-timeout plugin).
        reruns: Number of times to retry failed tests (pytest-rerunfailures plugin).
        reruns_delay: Delay in seconds between retries (pytest-rerunfailures plugin).
    """

    # Constants for special modes
    _SPECIAL_MODES = [
        PytestSpecialMode.LIST_PLUGINS,
        PytestSpecialMode.CHECK_PLUGINS,
        PytestSpecialMode.COLLECT_ONLY,
        PytestSpecialMode.LIST_FIXTURES,
        PytestSpecialMode.LIST_MARKERS,
        PytestSpecialMode.PARAMETRIZE_HELP,
    ]

    verbose: bool | None = field(default=None)
    tb: str | None = field(default=None)
    maxfail: int | None = field(default=None)
    no_header: bool | None = field(default=None)
    disable_warnings: bool | None = field(default=None)
    json_report: bool | None = field(default=None)
    junitxml: str | None = field(default=None)
    slow_test_threshold: float | None = field(default=None)
    total_time_warning: float | None = field(default=None)
    workers: str | None = field(default=None)
    coverage_threshold: float | None = field(default=None)
    auto_junitxml: bool | None = field(default=None)
    detect_flaky: bool | None = field(default=None)
    flaky_min_runs: int | None = field(default=None)
    flaky_failure_rate: float | None = field(default=None)
    html_report: str | None = field(default=None)
    parallel_preset: str | None = field(default=None)
    list_plugins: bool | None = field(default=None)
    check_plugins: bool | None = field(default=None)
    required_plugins: str | None = field(default=None)
    coverage_html: str | None = field(default=None)
    coverage_xml: str | None = field(default=None)
    coverage_report: bool | None = field(default=None)
    coverage_term_missing: bool | None = field(default=None)
    collect_only: bool | None = field(default=None)
    list_fixtures: bool | None = field(default=None)
    fixture_info: str | None = field(default=None)
    list_markers: bool | None = field(default=None)
    parametrize_help: bool | None = field(default=None)
    show_progress: bool | None = field(default=None)
    timeout: int | None = field(default=None)
    reruns: int | None = field(default=None)
    reruns_delay: int | None = field(default=None)

    def set_options(self, **kwargs: Any) -> None:
        """Set pytest-specific options with validation.

        Args:
            **kwargs: Option key-value pairs to set.
        """
        # Extract only the options that belong to this configuration
        config_fields = {field.name for field in self.__dataclass_fields__.values()}

        # Coerce workers to string when passed as int via CLI parsing
        if "workers" in kwargs and isinstance(kwargs.get("workers"), int):
            kwargs = kwargs.copy()
            kwargs["workers"] = str(kwargs["workers"])

        # Validate all options using extracted validator
        validate_pytest_options(
            **{k: v for k, v in kwargs.items() if k in config_fields},
        )

        # Set default junitxml if auto_junitxml is enabled and junitxml not
        # explicitly set
        junitxml = kwargs.get("junitxml")
        auto_junitxml = kwargs.get("auto_junitxml")
        if junitxml is None and (auto_junitxml is None or auto_junitxml):
            junitxml = "report.xml"
            kwargs = kwargs.copy()
            kwargs["junitxml"] = junitxml

        # Update the dataclass fields
        for key, value in kwargs.items():
            if key in config_fields:
                setattr(self, key, value)

    def get_options_dict(self) -> dict[str, Any]:
        """Get a dictionary of all non-None options.

        Returns:
            Dict[str, Any]: Dictionary of option key-value pairs, excluding None values.
        """
        options = {}
        for field_name, _field_info in self.__dataclass_fields__.items():
            value = getattr(self, field_name)
            if value is not None:
                options[field_name] = value
        return options

    def is_special_mode(self) -> bool:
        """Check if any special mode is enabled.

        Special modes are modes that don't run tests but perform other operations
        like listing plugins, fixtures, etc.

        Returns:
            bool: True if any special mode is enabled.
        """
        # Check boolean special modes
        if any(getattr(self, mode.value, False) for mode in self._SPECIAL_MODES):
            return True

        # Check fixture_info (string value, not boolean)
        return bool(getattr(self, "fixture_info", None))

    def get_special_mode(self) -> str | None:
        """Get the active special mode, if any.

        Returns:
            str | None: Name of the active special mode, or None if no special mode.
        """
        for mode in self._SPECIAL_MODES:
            if getattr(self, mode.value, False):
                return mode.value

        # Check for fixture_info (string value, not boolean)
        if getattr(self, PytestSpecialMode.FIXTURE_INFO.value, None):
            return PytestSpecialMode.FIXTURE_INFO.value

        return None

    def get_special_mode_value(self, mode: str) -> Any:
        """Get the value for a special mode.

        Args:
            mode: The special mode name.

        Returns:
            Any: The value associated with the special mode.
        """
        if mode == PytestSpecialMode.FIXTURE_INFO.value:
            return self.fixture_info
        elif mode == PytestSpecialMode.CHECK_PLUGINS.value:
            return self.required_plugins
        else:
            return getattr(self, mode, False)
