"""Shared fixtures for CLI tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from tests.constants import EXIT_SUCCESS

if TYPE_CHECKING:
    pass


@pytest.fixture
def isolated_cli_runner() -> CliRunner:
    """Click CLI test runner with isolated filesystem.

    Returns:
        A CliRunner instance with isolated filesystem.
    """
    return CliRunner(mix_stderr=False)


@pytest.fixture
def mock_run_lint_tools_simple() -> Generator[MagicMock, None, None]:
    """Mock the run_lint_tools_simple function used by check/format commands.

    Yields:
        MagicMock: A MagicMock instance for the mocked function.
    """
    with (
        patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock_check,
        patch(
            "lintro.cli_utils.commands.format.run_lint_tools_simple",
        ) as mock_format,
    ):
        # Configure both mocks to return 0 by default
        mock_check.return_value = EXIT_SUCCESS
        mock_format.return_value = EXIT_SUCCESS
        yield mock_check


@pytest.fixture
def mock_run_lint_tools_check() -> Generator[MagicMock, None, None]:
    """Mock run_lint_tools_simple specifically for check command.

    Yields:
        MagicMock: A MagicMock instance for the mocked check function.
    """
    with patch("lintro.cli_utils.commands.check.run_lint_tools_simple") as mock:
        mock.return_value = EXIT_SUCCESS
        yield mock


@pytest.fixture
def mock_run_lint_tools_format() -> Generator[MagicMock, None, None]:
    """Mock run_lint_tools_simple specifically for format command.

    Yields:
        MagicMock: A MagicMock instance for the mocked format function.
    """
    with patch("lintro.cli_utils.commands.format.run_lint_tools_simple") as mock:
        mock.return_value = EXIT_SUCCESS
        yield mock


@pytest.fixture
def mock_tool_registry() -> Generator[MagicMock, None, None]:
    """Mock ToolRegistry with common tools.

    Yields:
        MagicMock: A MagicMock instance for the ToolRegistry.
    """
    with patch("lintro.plugins.registry.ToolRegistry") as mock:
        mock.get_names.return_value = ["ruff", "black", "mypy", "pytest"]
        mock.is_registered.return_value = True
        mock.get.return_value = MagicMock()
        yield mock


@pytest.fixture
def mock_subprocess_success() -> Generator[MagicMock, None, None]:
    """Mock successful subprocess execution.

    Yields:
        MagicMock: A MagicMock instance for subprocess.run.
    """
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(
            returncode=EXIT_SUCCESS,
            stdout="",
            stderr="",
        )
        yield mock
