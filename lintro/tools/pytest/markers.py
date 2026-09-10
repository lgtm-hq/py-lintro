"""Pytest marker and plugin utility functions."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess used safely with shell=False
from typing import TYPE_CHECKING

from loguru import logger

from lintro.parsers.base_parser import strip_ansi_codes

if TYPE_CHECKING:
    from lintro.tools.pytest.definition import PytestPlugin


def check_plugin_installed(plugin_name: str) -> bool:
    """Check if a pytest plugin is installed.

    Checks for the plugin using importlib.metadata, trying both the exact name
    and an alternative name with hyphens replaced by underscores (e.g., "pytest-cov"
    and "pytest_cov").

    Args:
        plugin_name: Name of the plugin to check (e.g., 'pytest-cov', 'pytest-xdist').

    Returns:
        bool: True if plugin is installed (found under either name), False otherwise.

    Examples:
        >>> check_plugin_installed("pytest-cov")
        True  # if pytest-cov is installed
        >>> check_plugin_installed("pytest-nonexistent")
        False
    """
    import importlib.metadata

    # Try to find the plugin package
    try:
        importlib.metadata.distribution(plugin_name)
        return True
    except importlib.metadata.PackageNotFoundError:
        # Try alternative names (e.g., pytest-cov -> pytest_cov)
        alt_name = plugin_name.replace("-", "_")
        try:
            importlib.metadata.distribution(alt_name)
            return True
        except importlib.metadata.PackageNotFoundError:
            return False


def list_installed_plugins() -> list[dict[str, str]]:
    """List all installed pytest plugins.

    Scans all installed Python packages and filters for those whose names start
    with "pytest-" or "pytest_". Returns plugin information including name and version.

    Returns:
        list[dict[str, str]]: List of plugin information dictionaries, each containing:
            - 'name': Plugin package name (e.g., "pytest-cov")
            - 'version': Plugin version string (e.g., "4.1.0")
        List is sorted alphabetically by plugin name.

    Examples:
        >>> plugins = list_installed_plugins()
        >>> [p['name'] for p in plugins if 'cov' in p['name']]
        ['pytest-cov']
    """
    plugins: list[dict[str, str]] = []

    import importlib.metadata

    # Get all installed packages
    distributions = importlib.metadata.distributions()

    # Filter for pytest plugins
    for dist in distributions:
        dist_name = dist.metadata["Name"] or ""
        if dist_name.startswith("pytest-") or dist_name.startswith("pytest_"):
            version = dist.metadata["Version"] or "unknown"
            plugins.append({"name": dist_name, "version": version})

    # Sort by name
    plugins.sort(key=lambda x: x["name"])
    return plugins


def get_pytest_version_info() -> str:
    """Get pytest version and plugin information.

    Executes `pytest --version` to retrieve version information. Handles errors
    gracefully by returning a fallback message if the command fails.

    Returns:
        str: Formatted string with pytest version information from stdout.
            Returns "pytest version information unavailable" if the command
            fails or times out.

    Examples:
        >>> version_info = get_pytest_version_info()
        >>> "pytest" in version_info.lower()
        True
    """
    try:
        cmd = ["pytest", "--version"]
        result = subprocess.run(  # nosec B603 - pytest is a trusted executable
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"Failed to get pytest version: {e}")
        return "pytest version information unavailable"


def collect_tests_once(
    tool: PytestPlugin,
    target_files: list[str],
) -> int:
    """Collect tests and return total count.

    This function runs pytest --collect-only to count all available tests.

    Args:
        tool: PytestTool instance with _get_executable_command and _run_subprocess
            methods. Must support running pytest commands.
        target_files: List of file paths or directory paths to check for tests.
            These are passed directly to pytest --collect-only.

    Returns:
        int: Total number of tests found. Returns 0 if collection fails.

    Examples:
        >>> tool = PytestTool(...)
        >>> total = collect_tests_once(tool, ["tests/"])
        >>> total >= 0
        True
    """
    import re

    try:
        # Use pytest --collect-only to list all tests
        collect_cmd = tool._get_executable_command(tool_name="pytest")
        collect_cmd.append("--collect-only")
        collect_cmd.append("-q")  # Quiet mode for faster collection
        collect_cmd.extend(target_files)

        logger.debug(f"Collecting tests with command: {' '.join(collect_cmd)}")

        success, output = tool._run_subprocess(collect_cmd)
        if not success:
            # Log the failure with output to aid debugging
            output_preview = output[:500] if output else "(no output)"
            logger.warning(
                f"Test collection failed (exit non-zero). "
                f"Command: {' '.join(collect_cmd[:5])}... "
                f"Output: {output_preview}",
            )
            return 0

        # Extract the total count from collection output
        # Formats: "collected XXXX items", "XXXX tests collected in Y.YYs"
        # Strip ANSI escape codes first to avoid color codes breaking regex
        cleaned_output = strip_ansi_codes(output or "")
        total_count = 0
        match = re.search(r"collected\s+(\d+)\s+items?", cleaned_output)
        if not match:
            match = re.search(r"(\d+)\s+tests?\s+collected", cleaned_output)
        if match:
            total_count = int(match.group(1))
        else:
            # Log when we can't parse the output (use original for debugging)
            output_preview = output[:300] if output else "(no output)"
            logger.debug(
                f"Could not parse test count from collection output: {output_preview}",
            )

        return total_count
    except (OSError, ValueError, RuntimeError) as e:
        logger.debug(f"Failed to collect tests: {e}")
        return 0


def get_total_test_count(
    tool: PytestPlugin,
    target_files: list[str],
) -> int:
    """Get total count of all available tests.

    This function delegates to collect_tests_once().

    Args:
        tool: PytestTool instance with _get_executable_command and _run_subprocess
            methods. Must support running pytest commands.
        target_files: List of file paths or directory paths to check for tests.
            These are passed directly to pytest --collect-only.

    Returns:
        int: Total number of tests that exist.
            Returns 0 if collection fails or no tests are found.

    Examples:
        >>> tool = PytestTool(...)
        >>> count = get_total_test_count(tool, ["tests/"])
        >>> count >= 0
        True
    """
    return collect_tests_once(tool, target_files)
