"""Shared fixtures for tool unit tests.

Note: This file contains core fixtures only. Additional fixtures and tests
have been split into separate files:
- tests/unit/tools/assertions/conftest.py - Assertion helper fixtures
- tests/unit/tools/test_plugin_definitions.py - Parametrized definition tests
- tests/unit/tools/test_common_behaviors.py - Common tool behavior tests
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from lintro.enums.tool_name import ToolName
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin, ExecutionContext

if TYPE_CHECKING:
    from lintro.tools.definitions.clippy import ClippyPlugin
    from lintro.tools.definitions.mypy import MypyPlugin
    from lintro.tools.definitions.tsc import TscPlugin


@pytest.fixture
def mock_subprocess_run() -> Generator[MagicMock, None, None]:
    """Mock subprocess.run for tool testing.

    Yields:
        MagicMock: Configured mock for subprocess operations.
    """
    with patch("subprocess.run") as mock_run:
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = ""
        mock_process.stderr = ""
        mock_run.return_value = mock_process
        yield mock_run


@pytest.fixture
def mock_tool_config() -> dict[str, Any]:
    """Provide a mock tool configuration.

    Returns:
        dict: Sample tool configuration dictionary.
    """
    return {
        "priority": 50,
        "file_patterns": ["*.py"],
        "tool_type": "linter",
        "options": {
            "timeout": 30,
            "line_length": 88,
        },
    }


@pytest.fixture
def mock_tool_result() -> Mock:
    """Provide a mock tool result.

    Returns:
        Mock: Configured mock tool result with default values.
    """
    result = Mock()
    result.name = "test_tool"
    result.success = True
    result.output = ""
    result.issues_count = 0
    result.issues = []
    return result


@pytest.fixture
def mypy_plugin() -> MypyPlugin:
    """Provide a MypyPlugin instance for testing.

    Returns:
        A MypyPlugin instance.
    """
    from lintro.tools.definitions.mypy import MypyPlugin

    return MypyPlugin()


@pytest.fixture
def clippy_plugin() -> ClippyPlugin:
    """Provide a ClippyPlugin instance for testing.

    Returns:
        A ClippyPlugin instance.
    """
    from lintro.tools.definitions.clippy import ClippyPlugin

    return ClippyPlugin()


@pytest.fixture
def tsc_plugin() -> TscPlugin:
    """Provide a TscPlugin instance for testing.

    Returns:
        A TscPlugin instance.
    """
    from lintro.tools.definitions.tsc import TscPlugin

    return TscPlugin()


# -----------------------------------------------------------------------------
# Shared patch fixtures for subprocess and tool availability
# -----------------------------------------------------------------------------


@pytest.fixture
def patch_subprocess_success() -> Callable[[str, int], Any]:
    """Factory for patching subprocess with success result.

    Returns:
        A factory function that creates a context manager to patch subprocess.

    Example:
        def test_something(patch_subprocess_success):
            with patch_subprocess_success(output="OK"):
                # subprocess.run will return success with "OK" output
    """
    from contextlib import contextmanager

    @contextmanager
    def _patch(output: str = "", returncode: int = 0) -> Generator[MagicMock]:
        mock_result = MagicMock()
        mock_result.stdout = output
        mock_result.stderr = ""
        mock_result.returncode = returncode
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            yield mock_run

    return _patch


@pytest.fixture
def patch_tool_available() -> Callable[[], Any]:
    """Factory for patching tool availability to return True.

    Returns:
        A factory function that creates a context manager for patching.

    Example:
        def test_something(patch_tool_available):
            with patch_tool_available():
                # _check_tool_available will return True
    """
    from contextlib import contextmanager

    @contextmanager
    def _patch() -> Generator[MagicMock]:
        with patch.object(
            BaseToolPlugin,
            "_check_tool_available",
            return_value=True,
        ) as mock_check:
            yield mock_check

    return _patch


# -----------------------------------------------------------------------------
# Mock tool factory fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_tool_factory() -> Callable[..., MagicMock]:
    """Factory for creating mock tool plugin instances.

    Returns:
        A factory function that creates configured MagicMock objects
        that behave like tool plugins.

    Example:
        def test_something(mock_tool_factory):
            mock_tool = mock_tool_factory(
                name=ToolName.RUFF,
                file_patterns=["*.py"],
                can_fix=True,
            )
            assert mock_tool.definition.name == ToolName.RUFF
    """

    def _create(
        name: ToolName = ToolName.RUFF,
        file_patterns: list[str] | None = None,
        can_fix: bool = True,
        timeout: int = 30,
        options: dict[str, Any] | None = None,
        exclude_patterns: list[str] | None = None,
        include_venv: bool = False,
        executable_command: list[str] | None = None,
        cwd: str = "/test/project",
    ) -> MagicMock:
        tool = MagicMock()
        tool.definition.name = name
        tool.definition.file_patterns = file_patterns or ["*.py"]
        tool.definition.can_fix = can_fix
        tool.options = options or {"timeout": timeout}
        tool.exclude_patterns = exclude_patterns or []
        tool.include_venv = include_venv
        tool._default_timeout = timeout

        # Mock common methods
        tool._get_executable_command.return_value = executable_command or [
            str(name).lower(),
        ]
        tool._verify_tool_version.return_value = None
        tool._validate_paths.return_value = None
        tool._get_cwd.return_value = cwd
        tool._build_config_args.return_value = []
        tool._get_enforced_settings.return_value = {}

        return tool

    return _create


# -----------------------------------------------------------------------------
# Mock execution context fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_execution_context_factory() -> Callable[..., MagicMock]:
    """Factory for creating mock ExecutionContext instances.

    Returns:
        A factory function that creates configured MagicMock objects
        that behave like ExecutionContext.

    Example:
        def test_something(mock_execution_context_factory):
            ctx = mock_execution_context_factory(files=["test.py"])
            assert ctx.files == ["test.py"]
    """

    def _create(
        files: list[str] | None = None,
        rel_files: list[str] | None = None,
        cwd: str | None = None,
        early_result: ToolResult | None = None,
        timeout: int | None = None,
        should_skip: bool = False,
    ) -> MagicMock:
        ctx = MagicMock(spec=ExecutionContext)
        ctx.files = files if files is not None else []
        ctx.rel_files = rel_files if rel_files is not None else []
        ctx.cwd = cwd
        ctx.early_result = early_result
        ctx.timeout = timeout if timeout is not None else 30
        # should_skip is True if explicitly set OR if early_result is provided
        ctx.should_skip = should_skip or (early_result is not None)
        return ctx

    return _create


@pytest.fixture
def mock_execution_context_for_tool(
    mock_execution_context_factory: Callable[..., MagicMock],
) -> Callable[..., MagicMock]:
    """Alias for mock_execution_context_factory for tool tests.

    This provides backward compatibility for tests using the old fixture name.

    Args:
        mock_execution_context_factory: Factory function for creating mock
            execution contexts.

    Returns:
        The same factory function as mock_execution_context_factory.
    """
    return mock_execution_context_factory
