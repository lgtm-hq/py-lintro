"""Option validation functions for pytest tool.

This module contains validation logic extracted from PytestTool.set_options()
to improve maintainability and reduce file size.
"""

from lintro.tools.pytest.collection import (
    get_parallel_workers_from_preset,
)


def validate_pytest_options(
    verbose: bool | None = None,
    tb: str | None = None,
    maxfail: int | None = None,
    no_header: bool | None = None,
    disable_warnings: bool | None = None,
    json_report: bool | None = None,
    junitxml: str | None = None,
    slow_test_threshold: float | None = None,
    total_time_warning: float | None = None,
    workers: str | None = None,
    coverage_threshold: float | None = None,
    auto_junitxml: bool | None = None,
    detect_flaky: bool | None = None,
    flaky_min_runs: int | None = None,
    flaky_failure_rate: float | None = None,
    html_report: str | None = None,
    parallel_preset: str | None = None,
    list_plugins: bool | None = None,
    check_plugins: bool | None = None,
    required_plugins: str | None = None,
    coverage_html: str | None = None,
    coverage_xml: str | None = None,
    coverage_report: bool | None = None,
    coverage_term_missing: bool | None = None,
    collect_only: bool | None = None,
    list_fixtures: bool | None = None,
    fixture_info: str | None = None,
    list_markers: bool | None = None,
    parametrize_help: bool | None = None,
    show_progress: bool | None = None,
    timeout: int | None = None,
    reruns: int | None = None,
    reruns_delay: int | None = None,
) -> None:
    """Validate pytest-specific options.

    Args:
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
        show_progress: Show progress during test execution.
        timeout: Timeout in seconds for individual tests (pytest-timeout plugin).
        reruns: Number of times to retry failed tests (pytest-rerunfailures plugin).
        reruns_delay: Delay in seconds between retries (pytest-rerunfailures plugin).

    Raises:
        ValueError: If an option value is invalid.
    """
    if verbose is not None and not isinstance(verbose, bool):
        raise ValueError("verbose must be a boolean")

    if tb is not None and tb not in ("short", "long", "auto", "line", "native"):
        raise ValueError("tb must be one of: short, long, auto, line, native")

    if maxfail is not None and (not isinstance(maxfail, int) or maxfail <= 0):
        raise ValueError("maxfail must be a positive integer")

    if no_header is not None and not isinstance(no_header, bool):
        raise ValueError("no_header must be a boolean")

    if disable_warnings is not None and not isinstance(disable_warnings, bool):
        raise ValueError("disable_warnings must be a boolean")

    if json_report is not None and not isinstance(json_report, bool):
        raise ValueError("json_report must be a boolean")

    if junitxml is not None and not isinstance(junitxml, str):
        raise ValueError("junitxml must be a string")

    if slow_test_threshold is not None and (
        not isinstance(slow_test_threshold, (int, float)) or slow_test_threshold < 0
    ):
        raise ValueError("slow_test_threshold must be a non-negative number")

    if total_time_warning is not None and (
        not isinstance(total_time_warning, (int, float)) or total_time_warning < 0
    ):
        raise ValueError("total_time_warning must be a non-negative number")

    if workers is not None and not isinstance(workers, (str, int)):
        raise ValueError(
            f"workers must be a string or integer (e.g., 'auto', '2', 4), "
            f"got {type(workers).__name__}: {workers!r}",
        )

    if coverage_threshold is not None and not isinstance(
        coverage_threshold,
        (int, float),
    ):
        raise ValueError("coverage_threshold must be a number")
    if coverage_threshold is not None and not (0 <= coverage_threshold <= 100):
        raise ValueError("coverage_threshold must be between 0 and 100")

    if auto_junitxml is not None and not isinstance(auto_junitxml, bool):
        raise ValueError("auto_junitxml must be a boolean")

    if detect_flaky is not None and not isinstance(detect_flaky, bool):
        raise ValueError("detect_flaky must be a boolean")

    if flaky_min_runs is not None and (
        not isinstance(flaky_min_runs, int) or flaky_min_runs < 1
    ):
        raise ValueError("flaky_min_runs must be a positive integer")

    if flaky_failure_rate is not None:
        if not isinstance(flaky_failure_rate, (int, float)):
            raise ValueError("flaky_failure_rate must be a number")
        if not (0 <= flaky_failure_rate <= 1):
            raise ValueError("flaky_failure_rate must be between 0 and 1")

    if html_report is not None and not isinstance(html_report, str):
        raise ValueError("html_report must be a string (path to HTML report)")

    if parallel_preset is not None and not isinstance(parallel_preset, str):
        raise ValueError("parallel_preset must be a string")
    # Validate preset value
    if parallel_preset is not None:
        try:
            get_parallel_workers_from_preset(parallel_preset)
        except ValueError as e:
            raise ValueError(f"Invalid parallel_preset: {e}") from e

    # Validate plugin options
    if list_plugins is not None and not isinstance(list_plugins, bool):
        raise ValueError("list_plugins must be a boolean")

    if check_plugins is not None and not isinstance(check_plugins, bool):
        raise ValueError("check_plugins must be a boolean")

    if required_plugins is not None and not isinstance(required_plugins, str):
        raise ValueError("required_plugins must be a string")

    # Validate coverage options
    if coverage_html is not None and not isinstance(coverage_html, str):
        raise ValueError("coverage_html must be a string")

    if coverage_xml is not None and not isinstance(coverage_xml, str):
        raise ValueError("coverage_xml must be a string")

    if coverage_report is not None and not isinstance(coverage_report, bool):
        raise ValueError("coverage_report must be a boolean")

    if coverage_term_missing is not None and not isinstance(
        coverage_term_missing,
        bool,
    ):
        raise ValueError("coverage_term_missing must be a boolean")

    # Validate discovery and inspection options
    if collect_only is not None and not isinstance(collect_only, bool):
        raise ValueError("collect_only must be a boolean")

    if list_fixtures is not None and not isinstance(list_fixtures, bool):
        raise ValueError("list_fixtures must be a boolean")

    if fixture_info is not None and not isinstance(fixture_info, str):
        raise ValueError("fixture_info must be a string")

    if list_markers is not None and not isinstance(list_markers, bool):
        raise ValueError("list_markers must be a boolean")

    if parametrize_help is not None and not isinstance(parametrize_help, bool):
        raise ValueError("parametrize_help must be a boolean")

    if show_progress is not None and not isinstance(show_progress, bool):
        raise ValueError("show_progress must be a boolean")

    # Validate plugin-specific options
    if timeout is not None and (not isinstance(timeout, int) or timeout <= 0):
        raise ValueError("timeout must be a positive integer (seconds)")

    if reruns is not None and (not isinstance(reruns, int) or reruns < 0):
        raise ValueError("reruns must be a non-negative integer")

    if reruns_delay is not None and (
        not isinstance(reruns_delay, int) or reruns_delay < 0
    ):
        raise ValueError("reruns_delay must be a non-negative integer (seconds)")
