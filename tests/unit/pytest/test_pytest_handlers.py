"""Unit tests for pytest_handlers module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.tools.pytest.pytest_handlers import (
    handle_check_plugins,
    handle_collect_only,
    handle_fixture_info,
    handle_list_fixtures,
    handle_list_markers,
    handle_list_plugins,
    handle_parametrize_help,
)


@dataclass
class FakeToolDefinition:
    """Fake ToolDefinition for testing."""

    name: str = "pytest"


class FakePytestPlugin:
    """Fake PytestPlugin for testing handler functions."""

    def __init__(self) -> None:
        """Initialize fake plugin."""
        self._definition = FakeToolDefinition()
        self._subprocess_success = True
        self._subprocess_output = ""
        self._executable_cmd: list[str] = ["pytest"]

    @property
    def definition(self) -> FakeToolDefinition:
        """Return the tool definition.

        Returns:
            The tool definition.
        """
        return self._definition

    def _get_executable_command(self, tool_name: str = "pytest") -> list[str]:
        """Return command to execute the tool.

        Args:
            tool_name: Name of the tool to execute.

        Returns:
            List of command arguments.
        """
        return list(self._executable_cmd)

    def _run_subprocess(self, cmd: list[str]) -> tuple[bool, str]:
        """Run subprocess and return success/output.

        Args:
            cmd: Command to execute.

        Returns:
            Tuple of (success, output).
        """
        return self._subprocess_success, self._subprocess_output


@pytest.fixture
def fake_pytest_plugin() -> FakePytestPlugin:
    """Create a FakePytestPlugin instance for testing.

    Returns:
        A FakePytestPlugin instance.
    """
    return FakePytestPlugin()


# Tests for handle_list_plugins


@patch("lintro.tools.pytest.pytest_handlers.list_installed_plugins")
@patch("lintro.tools.pytest.pytest_handlers.get_pytest_version_info")
def test_list_plugins_with_results(
    mock_version: MagicMock,
    mock_plugins: MagicMock,
    fake_pytest_plugin: FakePytestPlugin,
) -> None:
    """List installed plugins with version info.

    Args:
        mock_version: Mock for pytest version info.
        mock_plugins: Mock for installed plugins list.
        fake_pytest_plugin: The fake pytest plugin instance to test.
    """
    mock_version.return_value = "pytest 7.0.0"
    mock_plugins.return_value = [
        {"name": "pytest-cov", "version": "4.0.0"},
        {"name": "pytest-mock", "version": "3.10.0"},
    ]

    result = handle_list_plugins(fake_pytest_plugin)  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("pytest 7.0.0")
    assert_that(result.output).contains("Installed pytest plugins (2)")
    assert_that(result.output).contains("pytest-cov (4.0.0)")
    assert_that(result.output).contains("pytest-mock (3.10.0)")


@patch("lintro.tools.pytest.pytest_handlers.list_installed_plugins")
@patch("lintro.tools.pytest.pytest_handlers.get_pytest_version_info")
def test_list_plugins_no_plugins(
    mock_version: MagicMock,
    mock_plugins: MagicMock,
    fake_pytest_plugin: FakePytestPlugin,
) -> None:
    """Show message when no plugins found.

    Args:
        mock_version: Mock for the version check function.
        mock_plugins: Mock for the plugins listing function.
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    mock_version.return_value = "pytest 7.0.0"
    mock_plugins.return_value = []

    result = handle_list_plugins(fake_pytest_plugin)  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("No pytest plugins found")


# Tests for handle_check_plugins


@patch("lintro.tools.pytest.pytest_handlers.check_plugin_installed")
def test_check_all_plugins_installed(
    mock_check: MagicMock,
    fake_pytest_plugin: FakePytestPlugin,
) -> None:
    """All required plugins are installed.

    Args:
        mock_check: Mock for the plugin installation check function.
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    mock_check.return_value = True

    result = handle_check_plugins(fake_pytest_plugin, "pytest-cov,pytest-mock")  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("Installed plugins (2)")
    assert_that(result.output).contains("pytest-cov")
    assert_that(result.output).contains("pytest-mock")


@patch("lintro.tools.pytest.pytest_handlers.check_plugin_installed")
def test_check_missing_plugins(
    mock_check: MagicMock,
    fake_pytest_plugin: FakePytestPlugin,
) -> None:
    """Some required plugins are missing.

    Args:
        mock_check: Mock for the plugin installation check function.
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    mock_check.side_effect = lambda p: p == "pytest-cov"

    result = handle_check_plugins(fake_pytest_plugin, "pytest-cov,pytest-xdist")  # type: ignore[arg-type]

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("Installed plugins (1)")
    assert_that(result.output).contains("Missing plugins (1)")
    assert_that(result.output).contains("pytest-xdist")
    assert_that(result.output).contains("pip install")


@patch("lintro.tools.pytest.pytest_handlers.check_plugin_installed")
def test_check_all_plugins_missing(
    mock_check: MagicMock,
    fake_pytest_plugin: FakePytestPlugin,
) -> None:
    """All required plugins are missing.

    Args:
        mock_check: Mock for the plugin installation check function.
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    mock_check.return_value = False

    result = handle_check_plugins(fake_pytest_plugin, "pytest-cov,pytest-xdist")  # type: ignore[arg-type]

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("Missing plugins (2)")


@pytest.mark.parametrize(
    ("plugins_input", "expected_message"),
    [
        (None, "required_plugins must be specified"),
        ("", "required_plugins must be specified"),
    ],
    ids=["none_plugins", "empty_plugins"],
)
def test_check_plugins_invalid_input(
    fake_pytest_plugin: FakePytestPlugin,
    plugins_input: str | None,
    expected_message: str,
) -> None:
    """Error when no plugins or empty plugins specified.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
        plugins_input: The plugins input to test.
        expected_message: Expected error message in the result.
    """
    result = handle_check_plugins(fake_pytest_plugin, plugins_input)  # type: ignore[arg-type]

    assert_that(result.success).is_false()


@patch("lintro.tools.pytest.pytest_handlers.check_plugin_installed")
def test_check_plugins_with_whitespace(
    mock_check: MagicMock,
    fake_pytest_plugin: FakePytestPlugin,
) -> None:
    """Handle whitespace in plugin list.

    Args:
        mock_check: Mock for the plugin installation check function.
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    mock_check.return_value = True

    result = handle_check_plugins(fake_pytest_plugin, " pytest-cov , pytest-mock ")  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.output).is_not_none()
    assert_that("Installed plugins (2)" in result.output).is_true()  # type: ignore[operator]  # validated via is_not_none


# Tests for handle_collect_only


def test_collect_with_function_style_output(
    fake_pytest_plugin: FakePytestPlugin,
) -> None:
    """Parse <Function test_name> style output.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_output = """
<Module tests/test_example.py>
  <Function test_one>
  <Function test_two>
"""
    result = handle_collect_only(fake_pytest_plugin, ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("Collected 2 test(s)")
    assert_that(result.output).contains("test_one")
    assert_that(result.output).contains("test_two")


def test_collect_with_double_colon_style(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Parse test_file.py::test_name style output.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_output = """
tests/test_example.py::test_one
tests/test_example.py::test_two
"""
    result = handle_collect_only(fake_pytest_plugin, ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("Collected 2 test(s)")
    assert_that(result.output).contains("test_one")
    assert_that(result.output).contains("test_two")


def test_collect_subprocess_failure(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Return failure when subprocess fails.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_success = False
    fake_pytest_plugin._subprocess_output = "No tests found"

    result = handle_collect_only(fake_pytest_plugin, ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_false()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("No tests found")


def test_collect_no_tests(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Handle empty test collection.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_output = "no tests collected"

    result = handle_collect_only(fake_pytest_plugin, ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("Collected 0 test(s)")


# Tests for handle_list_fixtures


def test_list_fixtures_success(fake_pytest_plugin: FakePytestPlugin) -> None:
    """List fixtures successfully.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_output = """
tmp_path -- A tmp_path fixture
capsys -- Capture stdout/stderr
"""
    result = handle_list_fixtures(fake_pytest_plugin, ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("tmp_path")
    assert_that(result.output).contains("capsys")


def test_list_fixtures_subprocess_failure(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Return failure when subprocess fails.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_success = False
    fake_pytest_plugin._subprocess_output = "Error occurred"

    result = handle_list_fixtures(fake_pytest_plugin, ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_false()


# Tests for handle_fixture_info


def test_fixture_info_found(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Get info for specific fixture.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_output = """
tmp_path -- Temp path fixture
    Return a temporary directory path object.

capsys -- Capture fixture
    Capture stdout/stderr.
"""
    result = handle_fixture_info(fake_pytest_plugin, "tmp_path", ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("tmp_path")
    assert_that(result.output).contains("Temp path fixture")


def test_fixture_info_not_found(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Show message when fixture not found.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_output = """
capsys -- Capture fixture
"""
    result = handle_fixture_info(fake_pytest_plugin, "nonexistent", ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_false()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("'nonexistent' not found")


def test_fixture_info_subprocess_failure(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Return failure when subprocess fails.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_success = False
    fake_pytest_plugin._subprocess_output = "Error"

    result = handle_fixture_info(fake_pytest_plugin, "tmp_path", ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_false()


def test_fixture_info_with_suffix_char(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Handle fixture name with suffix character.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_output = """
tmp_path:
    Return a temporary directory path object.

other_fixture -- Other
"""
    result = handle_fixture_info(fake_pytest_plugin, "tmp_path", ["tests/"])  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("tmp_path")


# Tests for handle_list_markers


def test_list_markers_success(fake_pytest_plugin: FakePytestPlugin) -> None:
    """List markers successfully.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_output = """
@pytest.mark.slow: marks tests as slow
@pytest.mark.skip: skip test
"""
    result = handle_list_markers(fake_pytest_plugin)  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("slow")
    assert_that(result.output).contains("skip")


def test_list_markers_subprocess_failure(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Return failure when subprocess fails.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    fake_pytest_plugin._subprocess_success = False
    fake_pytest_plugin._subprocess_output = "Error"

    result = handle_list_markers(fake_pytest_plugin)  # type: ignore[arg-type]

    assert_that(result.success).is_false()


# Tests for handle_parametrize_help


def test_parametrize_help_output(fake_pytest_plugin: FakePytestPlugin) -> None:
    """Return parametrization help text.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    result = handle_parametrize_help(fake_pytest_plugin)  # type: ignore[arg-type]

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("Pytest Parametrization Help")
    assert_that(result.output).contains("@pytest.mark.parametrize")
    assert_that(result.output).contains("Basic Usage")
    assert_that(result.output).contains("Example:")


def test_parametrize_help_contains_doc_link(
    fake_pytest_plugin: FakePytestPlugin,
) -> None:
    """Help text contains documentation link.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
    """
    result = handle_parametrize_help(fake_pytest_plugin)  # type: ignore[arg-type]

    assert_that(result.output).is_not_none()
    assert_that(result.output).contains("docs.pytest.org")


# Exception handling tests using parametrize


@pytest.mark.parametrize(
    ("handler_func", "handler_args", "expected_error_message"),
    [
        (handle_collect_only, (["tests/"],), "Error collecting tests"),
        (handle_list_fixtures, (["tests/"],), "Error listing fixtures"),
        (handle_fixture_info, ("tmp_path", ["tests/"]), "Error getting fixture info"),
        (handle_list_markers, (), "Error listing markers"),
    ],
    ids=[
        "collect_only_exception",
        "list_fixtures_exception",
        "fixture_info_exception",
        "list_markers_exception",
    ],
)
def test_handler_exception_handling(
    fake_pytest_plugin: FakePytestPlugin,
    handler_func: Any,
    handler_args: tuple[Any, ...],
    expected_error_message: str,
) -> None:
    """Handle exceptions gracefully across all handler functions.

    Args:
        fake_pytest_plugin: Fixture providing a FakePytestPlugin instance.
        handler_func: The handler function being tested.
        handler_args: Arguments to pass to the handler function.
        expected_error_message: Expected error message in the result.
    """

    def raise_error(cmd: list[str]) -> tuple[bool, str]:
        raise RuntimeError("Subprocess error")

    fake_pytest_plugin._run_subprocess = raise_error  # type: ignore[method-assign]

    result = handler_func(fake_pytest_plugin, *handler_args)

    assert_that(result.success).is_false()
    assert_that(result.output).is_not_none()
    assert_that(expected_error_message in result.output).is_true()
