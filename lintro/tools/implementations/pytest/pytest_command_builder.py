"""Command building functions for pytest tool.

This module contains command building logic extracted from PytestTool to improve
maintainability and reduce file size. Functions are organized by command section.
"""

import os
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.tools.implementations.pytest.collection import (
    get_parallel_workers_from_preset,
)
from lintro.tools.implementations.pytest.markers import check_plugin_installed

if TYPE_CHECKING:
    from lintro.tools.definitions.pytest import PytestPlugin

# Constants for pytest configuration
PYTEST_TEST_MODE_ENV: str = "LINTRO_TEST_MODE"
PYTEST_TEST_MODE_VALUE: str = "1"


def build_base_command(tool: "PytestPlugin") -> list[str]:
    """Build the base pytest command.

    Args:
        tool: PytestPlugin instance.

    Returns:
        list[str]: Base command list starting with pytest executable.
    """
    return tool._get_executable_command(tool_name="pytest")


def add_verbosity_options(cmd: list[str], options: dict[str, Any]) -> None:
    """Add verbosity and traceback options to command.

    Args:
        cmd: Command list to modify.
        options: Options dictionary.
    """
    # Add verbosity - ensure it's enabled if show_progress is True
    show_progress = options.get("show_progress", True)
    verbose = options.get("verbose", show_progress)  # Default to show_progress value
    if verbose or show_progress:
        cmd.append("-v")

    # Add traceback format
    tb_format = options.get("tb", "short")
    cmd.extend(["--tb", tb_format])

    # Add maxfail only if specified
    # Note: We default to None to avoid stopping early and run all tests
    maxfail = options.get("maxfail")
    if maxfail is not None:
        cmd.extend(["--maxfail", str(maxfail)])

    # Add no-header
    if options.get("no_header", True):
        cmd.append("--no-header")

    # Add disable-warnings
    if options.get("disable_warnings", True):
        cmd.append("--disable-warnings")


def add_output_options(cmd: list[str], options: dict[str, Any]) -> str | None:
    """Add output format options (JSON, JUnit XML, HTML) to command.

    Args:
        cmd: Command list to modify.
        options: Options dictionary.

    Returns:
        str | None: The junitxml path if auto-enabled, None otherwise.
    """
    # Add output format options
    if options.get("json_report", False):
        cmd.append("--json-report")
        cmd.append("--json-report-file=pytest-report.json")

    # Track if junitxml was explicitly provided
    junitxml_explicit = "junitxml" in options
    junitxml_value = options.get("junitxml")
    auto_junitxml_path: str | None = None

    if junitxml_value:
        # User provided a truthy value, use it
        cmd.extend(["--junitxml", junitxml_value])
    else:
        # Auto-enable junitxml to capture all test results including skipped tests
        # Only if user didn't explicitly disable it
        # (junitxml_explicit True but falsy value)
        auto_junitxml = options.get("auto_junitxml", True)
        if not junitxml_explicit and auto_junitxml:
            cmd.extend(["--junitxml", "report.xml"])
            auto_junitxml_path = "report.xml"
            logger.debug("Auto-enabled junitxml=report.xml to capture skipped tests")

    # Add pytest-html HTML report if specified
    html_report = options.get("html_report")
    if html_report:
        cmd.extend(["--html", html_report])
        logger.debug(f"HTML report enabled: {html_report}")

    return auto_junitxml_path


def add_parallel_options(cmd: list[str], options: dict[str, Any]) -> None:
    """Add parallel execution options to command.

    Args:
        cmd: Command list to modify.
        options: Options dictionary.
    """
    # Add pytest-xdist parallel execution
    # Priority: parallel_preset > workers > default (auto)
    workers = options.get("workers")
    parallel_preset = options.get("parallel_preset")
    if parallel_preset:
        # Convert preset to worker count
        workers = get_parallel_workers_from_preset(parallel_preset)
        logger.debug(
            f"Using parallel preset '{parallel_preset}' -> workers={workers}",
        )
    # Default to auto if not explicitly disabled (workers=0 or workers="0")
    if workers is None:
        workers = "auto"
    if workers and str(workers) != "0":
        cmd.extend(["-n", str(workers)])


def add_coverage_options(cmd: list[str], options: dict[str, Any]) -> None:
    """Add coverage options to command.

    Args:
        cmd: Command list to modify.
        options: Options dictionary.
    """
    # Add coverage threshold if specified
    coverage_threshold = options.get("coverage_threshold")
    if coverage_threshold is not None:
        cmd.extend(["--cov-fail-under", str(coverage_threshold)])

    # Add coverage report options (requires pytest-cov)
    coverage_html = options.get("coverage_html")
    coverage_xml = options.get("coverage_xml")
    coverage_report = options.get("coverage_report", False)
    coverage_term_missing = options.get("coverage_term_missing", False)

    # If coverage_report is True, generate both HTML and XML
    if coverage_report:
        if not coverage_html:
            coverage_html = "htmlcov"
        if not coverage_xml:
            coverage_xml = "coverage.xml"

    # Add coverage collection if any coverage options are specified
    needs_coverage = (
        coverage_html
        or coverage_xml
        or coverage_term_missing
        or coverage_threshold is not None
    )
    if needs_coverage:
        # Add --cov flag to enable coverage collection
        # Default to current directory, but can be overridden
        cmd.append("--cov=.")

    # Add coverage HTML report
    if coverage_html:
        # pytest-cov uses --cov-report=html or --cov-report=html:dir
        # Only use default --cov-report=html for exact "htmlcov" match
        # Custom paths ending in "htmlcov" should use the custom directory format
        if coverage_html == "htmlcov":
            cmd.append("--cov-report=html")
        else:
            # Custom directory (remove trailing /index.html if present)
            html_dir = coverage_html.replace(
                "/index.html",
                "",
            ).replace("index.html", "")
            if html_dir:
                cmd.extend(["--cov-report", f"html:{html_dir}"])
            else:
                cmd.append("--cov-report=html")
        logger.debug(f"Coverage HTML report enabled: {coverage_html}")

    # Add coverage XML report
    if coverage_xml:
        # pytest-cov uses --cov-report=xml or --cov-report=xml:file
        # (without .xml extension)
        if coverage_xml == "coverage.xml":
            cmd.append("--cov-report=xml")
        else:
            # Custom file path (remove .xml extension for the flag)
            xml_file = (
                coverage_xml.replace(".xml", "")
                if coverage_xml.endswith(".xml")
                else coverage_xml
            )
            if xml_file:
                cmd.extend(["--cov-report", f"xml:{xml_file}"])
            else:
                cmd.append("--cov-report=xml")
        logger.debug(f"Coverage XML report enabled: {coverage_xml}")

    # Add terminal coverage report with missing lines
    if coverage_term_missing:
        cmd.append("--cov-report=term-missing")
        logger.debug("Coverage terminal report with missing lines enabled")


def add_test_mode_options(cmd: list[str]) -> None:
    """Add test mode isolation options to command.

    Args:
        cmd: Command list to modify.
    """
    # Add test mode isolation if in test mode
    if os.environ.get(PYTEST_TEST_MODE_ENV) == PYTEST_TEST_MODE_VALUE:
        cmd.append("--strict-markers")
        cmd.append("--strict-config")


def add_plugin_options(cmd: list[str], options: dict[str, Any]) -> None:
    """Add plugin-specific options to command.

    Args:
        cmd: Command list to modify.
        options: Options dictionary.
    """
    # Add pytest-timeout options if timeout is specified
    # Only add timeout arguments if pytest-timeout plugin is installed
    timeout = options.get("timeout")
    if timeout is not None:
        if check_plugin_installed("pytest-timeout"):
            cmd.extend(["--timeout", str(timeout)])
            # Default timeout method to 'signal' if not specified
            timeout_method = options.get("timeout_method", "signal")
            cmd.extend(["--timeout-method", timeout_method])
            logger.debug(f"Timeout enabled: {timeout}s (method: {timeout_method})")
        else:
            logger.warning(
                "pytest-timeout plugin not installed; timeout option ignored. "
                "Install with: pip install pytest-timeout",
            )

    # Add pytest-rerunfailures options
    reruns = options.get("reruns")
    if reruns is not None and reruns > 0:
        cmd.extend(["--reruns", str(reruns)])

        reruns_delay = options.get("reruns_delay")
        if reruns_delay is not None and reruns_delay > 0:
            cmd.extend(["--reruns-delay", str(reruns_delay)])
            logger.debug(f"Reruns enabled: {reruns} times with {reruns_delay}s delay")
        else:
            logger.debug(f"Reruns enabled: {reruns} times")


def add_ignore_options(cmd: list[str], tool: "PytestPlugin") -> None:
    """Add ignore options to command for exclude patterns.

    Args:
        cmd: Command list to modify.
        tool: PytestPlugin instance.
    """
    # Glob characters that pytest --ignore doesn't support
    # These patterns should be skipped as they can't be used with --ignore
    glob_chars = frozenset({"*", "?", "[", "]"})

    # Add --ignore flags for each exclude pattern
    for pattern in tool.exclude_patterns:
        # Skip patterns containing glob characters - pytest --ignore only works
        # with exact directory/file paths, not glob patterns
        if any(char in pattern for char in glob_chars):
            continue

        # pytest --ignore expects directory paths, not glob patterns
        # Convert glob patterns to directory paths where possible
        if pattern.endswith("/*"):
            # Remove /* from the end to get directory path
            ignore_path = pattern[:-2]
            cmd.extend(["--ignore", ignore_path])
        elif pattern.endswith("/"):
            # Pattern already ends with /, remove it
            ignore_path = pattern[:-1]
            cmd.extend(["--ignore", ignore_path])
        else:
            # For other patterns, try to use them as-is
            # pytest --ignore works with directory names
            cmd.extend(["--ignore", pattern])


def build_check_command(
    tool: "PytestPlugin",
    files: list[str],
    fix: bool = False,
) -> tuple[list[str], str | None]:
    """Build the pytest command.

    Args:
        tool: PytestPlugin instance.
        files: list[str]: List of files to test.
        fix: bool: Ignored for pytest (not applicable).

    Returns:
        tuple[list[str], str | None]: Tuple of (command arguments, auto junitxml path).
    """
    cmd = build_base_command(tool)

    # Add verbosity options
    add_verbosity_options(cmd, tool.options)

    # Add output options and capture auto-enabled junitxml path
    auto_junitxml_path = add_output_options(cmd, tool.options)

    # Add parallel options
    add_parallel_options(cmd, tool.options)

    # Add coverage options
    add_coverage_options(cmd, tool.options)

    # Add plugin options (timeout, reruns, etc.)
    add_plugin_options(cmd, tool.options)

    # Add test mode options
    add_test_mode_options(cmd)

    # Add ignore options for exclude patterns
    add_ignore_options(cmd, tool)

    # Add files
    cmd.extend(files)

    return cmd, auto_junitxml_path
