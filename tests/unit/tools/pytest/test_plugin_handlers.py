"""Tests for lintro.tools.implementations.pytest.pytest_handlers module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.implementations.pytest.pytest_handlers import (
    handle_check_plugins,
    handle_collect_only,
    handle_fixture_info,
    handle_list_fixtures,
    handle_list_markers,
    handle_list_plugins,
    handle_parametrize_help,
)

if TYPE_CHECKING:
    pass


def _make_mock_tool(name: str = "pytest") -> MagicMock:
    """Create a mock tool for testing.

    Args:
        name: Tool name for mock.

    Returns:
        MagicMock configured as a PytestPlugin.
    """
    mock_tool = MagicMock()
    mock_tool.definition.name = name
    mock_tool._get_executable_command.return_value = ["pytest"]
    return mock_tool


# =============================================================================
# handle_list_plugins tests
# =============================================================================


@patch("lintro.tools.implementations.pytest.pytest_handlers.get_pytest_version_info")
@patch("lintro.tools.implementations.pytest.pytest_handlers.list_installed_plugins")
def test_handle_list_plugins_with_plugins(
    mock_list: MagicMock,
    mock_version: MagicMock,
) -> None:
    """handle_list_plugins lists installed plugins.

    Args:
        mock_list: Mock for list_installed_plugins.
        mock_version: Mock for get_pytest_version_info.
    """
    mock_version.return_value = "pytest 7.4.0"
    mock_list.return_value = [
        {"name": "pytest-cov", "version": "4.1.0"},
        {"name": "pytest-mock", "version": "3.11.1"},
    ]
    mock_tool = _make_mock_tool()

    result = handle_list_plugins(mock_tool)

    assert_that(result.success).is_true()
    assert_that(result.output).contains("pytest 7.4.0")
    assert_that(result.output).contains("Installed pytest plugins (2):")
    assert_that(result.output).contains("pytest-cov (4.1.0)")
    assert_that(result.output).contains("pytest-mock (3.11.1)")


@patch("lintro.tools.implementations.pytest.pytest_handlers.get_pytest_version_info")
@patch("lintro.tools.implementations.pytest.pytest_handlers.list_installed_plugins")
def test_handle_list_plugins_no_plugins(
    mock_list: MagicMock,
    mock_version: MagicMock,
) -> None:
    """handle_list_plugins handles no plugins case.

    Args:
        mock_list: Mock for list_installed_plugins.
        mock_version: Mock for get_pytest_version_info.
    """
    mock_version.return_value = "pytest 7.4.0"
    mock_list.return_value = []
    mock_tool = _make_mock_tool()

    result = handle_list_plugins(mock_tool)

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No pytest plugins found")


# =============================================================================
# handle_check_plugins tests
# =============================================================================


@patch("lintro.tools.implementations.pytest.pytest_handlers.check_plugin_installed")
def test_handle_check_plugins_all_installed(mock_check: MagicMock) -> None:
    """handle_check_plugins reports all installed.

    Args:
        mock_check: Mock for check_plugin_installed.
    """
    mock_check.return_value = True
    mock_tool = _make_mock_tool()

    result = handle_check_plugins(mock_tool, "pytest-cov, pytest-mock")

    assert_that(result.success).is_true()
    assert_that(result.output).contains("Installed plugins (2)")
    assert_that(result.issues_count).is_equal_to(0)


@patch("lintro.tools.implementations.pytest.pytest_handlers.check_plugin_installed")
def test_handle_check_plugins_some_missing(mock_check: MagicMock) -> None:
    """handle_check_plugins reports missing plugins.

    Args:
        mock_check: Mock for check_plugin_installed.
    """
    mock_check.side_effect = [True, False]
    mock_tool = _make_mock_tool()

    result = handle_check_plugins(mock_tool, "pytest-cov, pytest-missing")

    assert_that(result.success).is_false()
    assert_that(result.output).contains("Missing plugins (1)")
    assert_that(result.output).contains("pytest-missing")
    assert_that(result.issues_count).is_equal_to(1)


def test_handle_check_plugins_no_plugins_specified() -> None:
    """handle_check_plugins errors when no plugins specified."""
    mock_tool = _make_mock_tool()

    result = handle_check_plugins(mock_tool, None)

    assert_that(result.success).is_false()
    assert_that(result.output).contains("required_plugins must be specified")


# =============================================================================
# handle_collect_only tests
# =============================================================================


def test_handle_collect_only_success() -> None:
    """handle_collect_only parses collected tests."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.return_value = (
        True,
        "<Function test_one>\n<Function test_two>\ntest_file.py::test_three",
    )

    result = handle_collect_only(mock_tool, ["tests/"])

    assert_that(result.success).is_true()
    assert_that(result.output).contains("Collected")
    assert_that(result.output).contains("test_one")


def test_handle_collect_only_failure() -> None:
    """handle_collect_only handles subprocess failure."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.return_value = (False, "Error message")

    result = handle_collect_only(mock_tool, ["tests/"])

    assert_that(result.success).is_false()
    assert_that(result.output).is_equal_to("Error message")


def test_handle_collect_only_exception() -> None:
    """handle_collect_only handles exceptions."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.side_effect = OSError("Command not found")

    result = handle_collect_only(mock_tool, ["tests/"])

    assert_that(result.success).is_false()
    assert_that(result.output).contains("Error collecting tests")


# =============================================================================
# handle_list_fixtures tests
# =============================================================================


def test_handle_list_fixtures_success() -> None:
    """handle_list_fixtures returns fixture output."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.return_value = (True, "tmp_path\ncaplog\nmonkeypatch")

    result = handle_list_fixtures(mock_tool, ["tests/"])

    assert_that(result.success).is_true()
    assert_that(result.output).contains("tmp_path")


def test_handle_list_fixtures_failure() -> None:
    """handle_list_fixtures handles failure."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.return_value = (False, "Error")

    result = handle_list_fixtures(mock_tool, ["tests/"])

    assert_that(result.success).is_false()


def test_handle_list_fixtures_exception() -> None:
    """handle_list_fixtures handles exceptions."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.side_effect = ValueError("Bad value")

    result = handle_list_fixtures(mock_tool, ["tests/"])

    assert_that(result.success).is_false()
    assert_that(result.output).contains("Error listing fixtures")


# =============================================================================
# handle_fixture_info tests
# =============================================================================


def test_handle_fixture_info_found() -> None:
    """handle_fixture_info finds fixture details."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.return_value = (
        True,
        "tmp_path -- Provides temporary directory\n"
        "    Scope: function\n"
        "caplog -- Captures logging output",
    )

    result = handle_fixture_info(mock_tool, "tmp_path", ["tests/"])

    assert_that(result.success).is_true()
    assert_that(result.output).contains("tmp_path")


def test_handle_fixture_info_not_found() -> None:
    """handle_fixture_info reports not found."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.return_value = (True, "other_fixture -- Some fixture")

    result = handle_fixture_info(mock_tool, "nonexistent", ["tests/"])

    assert_that(result.success).is_false()
    assert_that(result.output).contains("not found")


def test_handle_fixture_info_exception() -> None:
    """handle_fixture_info handles exceptions."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.side_effect = RuntimeError("Runtime error")

    result = handle_fixture_info(mock_tool, "tmp_path", ["tests/"])

    assert_that(result.success).is_false()
    assert_that(result.output).contains("Error getting fixture info")


# =============================================================================
# handle_list_markers tests
# =============================================================================


def test_handle_list_markers_success() -> None:
    """handle_list_markers returns markers output."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.return_value = (
        True,
        "@pytest.mark.skip\n@pytest.mark.parametrize",
    )

    result = handle_list_markers(mock_tool)

    assert_that(result.success).is_true()
    assert_that(result.output).contains("pytest.mark")


def test_handle_list_markers_failure() -> None:
    """handle_list_markers handles failure."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.return_value = (False, "Error")

    result = handle_list_markers(mock_tool)

    assert_that(result.success).is_false()


def test_handle_list_markers_exception() -> None:
    """handle_list_markers handles exceptions."""
    mock_tool = _make_mock_tool()
    mock_tool._run_subprocess.side_effect = OSError("Error")

    result = handle_list_markers(mock_tool)

    assert_that(result.success).is_false()
    assert_that(result.output).contains("Error listing markers")


# =============================================================================
# handle_parametrize_help tests
# =============================================================================


def test_handle_parametrize_help_returns_help_text() -> None:
    """handle_parametrize_help returns help text."""
    mock_tool = _make_mock_tool()

    result = handle_parametrize_help(mock_tool)

    assert_that(result.success).is_true()
    assert_that(result.output).contains("Parametrization Help")
    assert_that(result.output).contains("@pytest.mark.parametrize")
    assert_that(result.output).contains("Example:")
