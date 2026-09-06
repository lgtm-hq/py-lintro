"""Handler functions for pytest tool special modes.

This module contains handler functions extracted from PytestTool to improve
maintainability and reduce file size. These handlers implement special modes
like listing plugins, collecting tests, listing fixtures, etc.
"""

import re
import shlex
from typing import TYPE_CHECKING

from loguru import logger

from lintro.models.core.tool_result import ToolResult
from lintro.tools.pytest.markers import (
    check_plugin_installed,
    get_pytest_version_info,
    list_installed_plugins,
)

if TYPE_CHECKING:
    from lintro.tools.pytest.definition import PytestPlugin


def handle_list_plugins(tool: "PytestPlugin") -> ToolResult:
    """Handle list plugins mode.

    Args:
        tool: PytestTool instance.

    Returns:
        ToolResult: Results with plugin list.
    """
    plugins = list_installed_plugins()
    version_info = get_pytest_version_info()

    output_lines = [version_info, ""]
    if plugins:
        output_lines.append(f"Installed pytest plugins ({len(plugins)}):")
        for plugin in plugins:
            output_lines.append(f"  - {plugin['name']} ({plugin['version']})")
    else:
        output_lines.append("No pytest plugins found.")

    return ToolResult(
        name=tool.definition.name,
        success=True,
        issues=[],
        output="\n".join(output_lines),
        issues_count=0,
    )


def handle_check_plugins(
    tool: "PytestPlugin",
    required_plugins: str | None,
) -> ToolResult:
    """Handle check plugins mode.

    Args:
        tool: PytestTool instance.
        required_plugins: Comma-separated list of required plugin names.

    Returns:
        ToolResult: Results with plugin check status.
    """
    if not required_plugins:
        return ToolResult(
            name=tool.definition.name,
            success=False,
            issues=[],
            output=(
                "Error: required_plugins must be specified when check_plugins=True"
            ),
            issues_count=0,
        )

    plugin_list = [p.strip() for p in required_plugins.split(",") if p.strip()]
    missing_plugins: list[str] = []
    installed_plugins: list[str] = []

    for plugin in plugin_list:
        if check_plugin_installed(plugin):
            installed_plugins.append(plugin)
        else:
            missing_plugins.append(plugin)

    output_lines = []
    if installed_plugins:
        output_lines.append(f"✓ Installed plugins ({len(installed_plugins)}):")
        for plugin in installed_plugins:
            output_lines.append(f"  - {plugin}")

    if missing_plugins:
        output_lines.append(f"\n✗ Missing plugins ({len(missing_plugins)}):")
        for plugin in missing_plugins:
            output_lines.append(f"  - {plugin}")
        output_lines.append("\nInstall missing plugins with:")
        quoted_plugins = " ".join(shlex.quote(plugin) for plugin in missing_plugins)
        output_lines.append(f"  pip install {quoted_plugins}")

    success = len(missing_plugins) == 0

    return ToolResult(
        name=tool.definition.name,
        success=success,
        issues=[],
        output="\n".join(output_lines) if output_lines else "No plugins specified.",
        issues_count=len(missing_plugins),
    )


def handle_collect_only(
    tool: "PytestPlugin",
    target_files: list[str],
) -> ToolResult:
    """Handle collect-only mode.

    Args:
        tool: PytestTool instance.
        target_files: Files or directories to collect tests from.

    Returns:
        ToolResult: Results with collected test list.
    """
    try:
        collect_cmd = tool._get_executable_command(tool_name="pytest")
        collect_cmd.append("--collect-only")
        collect_cmd.extend(target_files)

        success, output = tool._run_subprocess(collect_cmd)
        if not success:
            return ToolResult(
                name=tool.definition.name,
                success=False,
                issues=[],
                output=output,
                issues_count=0,
            )

        # Parse collected tests from output
        test_list: list[str] = []
        for line in output.splitlines():
            line = line.strip()
            # Match test collection lines
            # (e.g., "<Function test_example>" or "test_file.py::test_function")
            if "<Function" in line or "::" in line:
                # Extract test identifier
                if "::" in line:
                    test_list.append(line.split("::")[-1].strip())
                elif "<Function" in line:
                    # Extract function name from <Function test_name>
                    match = re.search(r"<Function\s+(\w+)>", line)
                    if match:
                        test_list.append(match.group(1))

        output_lines = [f"Collected {len(test_list)} test(s):", ""]
        for test in test_list:
            output_lines.append(f"  - {test}")

        return ToolResult(
            name=tool.definition.name,
            success=True,
            issues=[],
            output="\n".join(output_lines),
            issues_count=0,
        )
    except (OSError, ValueError, RuntimeError) as e:
        logger.exception(f"Error collecting tests: {e}")
        return ToolResult(
            name=tool.definition.name,
            success=False,
            issues=[],
            output=f"Error collecting tests: {type(e).__name__}: {e}",
            issues_count=0,
        )


def handle_list_fixtures(
    tool: "PytestPlugin",
    target_files: list[str],
) -> ToolResult:
    """Handle list fixtures mode.

    Args:
        tool: PytestTool instance.
        target_files: Files or directories to collect fixtures from.

    Returns:
        ToolResult: Results with fixture list.
    """
    try:
        fixtures_cmd = tool._get_executable_command(tool_name="pytest")
        fixtures_cmd.extend(["--fixtures", "-q"])
        fixtures_cmd.extend(target_files)

        success, output = tool._run_subprocess(fixtures_cmd)
        if not success:
            return ToolResult(
                name=tool.definition.name,
                success=False,
                issues=[],
                output=output,
                issues_count=0,
            )

        return ToolResult(
            name=tool.definition.name,
            success=True,
            issues=[],
            output=output,
            issues_count=0,
        )
    except (OSError, ValueError, RuntimeError) as e:
        logger.exception(f"Error listing fixtures: {e}")
        return ToolResult(
            name=tool.definition.name,
            success=False,
            issues=[],
            output=f"Error listing fixtures: {type(e).__name__}: {e}",
            issues_count=0,
        )


def handle_fixture_info(
    tool: "PytestPlugin",
    fixture_name: str,
    target_files: list[str],
) -> ToolResult:
    """Handle fixture info mode.

    Args:
        tool: PytestTool instance.
        fixture_name: Name of fixture to get info for.
        target_files: Files or directories to search.

    Returns:
        ToolResult: Results with fixture information.
    """
    try:
        fixtures_cmd = tool._get_executable_command(tool_name="pytest")
        fixtures_cmd.extend(["--fixtures", "-v"])
        fixtures_cmd.extend(target_files)

        success, output = tool._run_subprocess(fixtures_cmd)
        if not success:
            return ToolResult(
                name=tool.definition.name,
                success=False,
                issues=[],
                output=output,
                issues_count=0,
            )

        # Extract fixture info for the specific fixture
        lines = output.splitlines()
        fixture_info_lines: list[str] = []
        in_fixture = False

        for line in lines:
            # Check if line starts with fixture name (pytest format)
            stripped_line = line.strip()
            if stripped_line.startswith(fixture_name) and (
                len(stripped_line) == len(fixture_name)
                or stripped_line[len(fixture_name)] in (" ", ":", "\n")
            ):
                in_fixture = True
                fixture_info_lines.append(line)
            elif in_fixture:
                if line.strip() and not line.startswith(" "):
                    # New fixture or section, stop
                    break
                fixture_info_lines.append(line)

        if fixture_info_lines:
            output_text = "\n".join(fixture_info_lines)
        else:
            output_text = f"Fixture '{fixture_name}' not found."

        return ToolResult(
            name=tool.definition.name,
            success=len(fixture_info_lines) > 0,
            issues=[],
            output=output_text,
            issues_count=0,
        )
    except (OSError, ValueError, RuntimeError) as e:
        logger.exception(f"Error getting fixture info: {e}")
        return ToolResult(
            name=tool.definition.name,
            success=False,
            issues=[],
            output=f"Error getting fixture info: {type(e).__name__}: {e}",
            issues_count=0,
        )


def handle_list_markers(tool: "PytestPlugin") -> ToolResult:
    """Handle list markers mode.

    Args:
        tool: PytestTool instance.

    Returns:
        ToolResult: Results with marker list.
    """
    try:
        markers_cmd = tool._get_executable_command(tool_name="pytest")
        markers_cmd.extend(["--markers"])

        success, output = tool._run_subprocess(markers_cmd)
        if not success:
            return ToolResult(
                name=tool.definition.name,
                success=False,
                issues=[],
                output=output,
                issues_count=0,
            )

        return ToolResult(
            name=tool.definition.name,
            success=True,
            issues=[],
            output=output,
            issues_count=0,
        )
    except (OSError, ValueError, RuntimeError) as e:
        logger.exception(f"Error listing markers: {e}")
        return ToolResult(
            name=tool.definition.name,
            success=False,
            issues=[],
            output=f"Error listing markers: {type(e).__name__}: {e}",
            issues_count=0,
        )


def handle_parametrize_help(tool: "PytestPlugin") -> ToolResult:
    """Handle parametrize help mode.

    Args:
        tool: PytestTool instance.

    Returns:
        ToolResult: Results with parametrization help.
    """
    help_text = """Pytest Parametrization Help

Parametrization allows you to run the same test with different inputs.

Basic Usage:
-----------
Use @pytest.mark.parametrize to provide multiple input values for a test function.
The test will run once for each set of parameters.

Example:
@pytest.mark.parametrize("input,expected", [(1, 2), (2, 4), (3, 6)])
def test_multiply(input, expected):
    assert input * 2 == expected

Multiple Parameters:
--------------------
You can parametrize multiple parameters at once by providing tuples of values.

Using Fixtures with Parametrization:
-------------------------------------
Parametrized tests can use fixtures. The parametrization runs for each fixture
instance, creating a cartesian product of parameters and fixtures.

Multiple Parametrizations:
--------------------------
You can stack multiple @pytest.mark.parametrize decorators to create a cartesian
product of all parameter combinations.

For detailed examples and advanced usage, see:
https://docs.pytest.org/en/stable/how-to/parametrize.html
"""
    return ToolResult(
        name=tool.definition.name,
        success=True,
        issues=[],
        output=help_text,
        issues_count=0,
    )
