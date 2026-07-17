"""Shared fixtures for ruff tool tests."""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from lintro.enums.tool_name import ToolName
from tests.test_samples_helpers import copy_sample

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.tools.definitions.ruff import RuffPlugin


def make_ruff_execution_context(
    *,
    files: list[str] | None = None,
    rel_files: list[str] | None = None,
    cwd: str | None = "/test/project",
    timeout: int = 30,
    should_skip: bool = False,
    early_result: ToolResult | None = None,
) -> MagicMock:
    """Build a mock ``ExecutionContext`` for ruff execution tests.

    Mirrors the shape returned by ``BaseToolPlugin._prepare_execution`` so
    ruff's ``execute_ruff_check``/``execute_ruff_fix`` helpers, which now route
    through that shared pipeline, can be exercised in isolation.

    Args:
        files: Absolute file paths the tool should process.
        rel_files: File paths relative to ``cwd``. Defaults to ``files``.
        cwd: Working directory for command execution.
        timeout: Timeout value in seconds.
        should_skip: Whether execution should short-circuit to ``early_result``.
        early_result: Result returned when ``should_skip`` is True.

    Returns:
        MagicMock: Object exposing the ``ExecutionContext`` attributes used by
        the ruff execution helpers.
    """
    resolved_files = ["test.py"] if files is None else files
    ctx = MagicMock()
    ctx.files = resolved_files
    ctx.rel_files = resolved_files if rel_files is None else rel_files
    ctx.cwd = cwd
    ctx.timeout = timeout
    ctx.should_skip = should_skip or (early_result is not None)
    ctx.early_result = early_result
    return ctx


@pytest.fixture
def ruff_execution_context() -> Callable[..., MagicMock]:
    """Provide a factory for mock ruff execution contexts.

    Returns:
        Callable[..., MagicMock]: Factory delegating to
        :func:`make_ruff_execution_context`.
    """
    return make_ruff_execution_context


@pytest.fixture
def mock_ruff_tool() -> MagicMock:
    """Provide a mock RuffPlugin instance for testing.

    Returns:
        MagicMock: Mock RuffPlugin instance with common attributes configured.
    """
    tool = MagicMock()
    tool.definition.name = ToolName.RUFF
    tool.definition.file_patterns = ["*.py", "*.pyi"]
    tool.definition.can_fix = True
    tool.options = {
        "timeout": 30,
        "format_check": False,
        "select": None,
        "ignore": None,
    }
    tool.exclude_patterns = []
    tool.include_venv = False
    tool._default_timeout = 30

    # Mock common methods
    tool._get_executable_command.return_value = ["ruff"]
    tool._verify_tool_version.return_value = None
    tool._validate_paths.return_value = None
    tool._get_cwd.return_value = "/test/project"
    tool._build_config_args.return_value = []
    tool._get_enforced_settings.return_value = {}

    # Ruff execution helpers now route through the shared
    # BaseToolPlugin._prepare_execution pipeline. Provide a sensible default
    # context (one file, no skip) that individual tests can override.
    tool._prepare_execution.return_value = make_ruff_execution_context()

    return tool


@pytest.fixture
def ruff_plugin() -> Generator[RuffPlugin, None, None]:
    """Provide a RuffPlugin instance for testing.

    Sets LINTRO_TEST_MODE environment variable to skip config loading.

    Yields:
        RuffPlugin: Configured RuffPlugin instance.
    """
    from lintro.tools.definitions.ruff import RuffPlugin

    with patch.dict(os.environ, {"LINTRO_TEST_MODE": "1"}):
        yield RuffPlugin()


@pytest.fixture
def sample_ruff_json_output() -> str:
    """Provide sample JSON output from ruff check.

    Returns:
        str: Sample JSON output with lint issues.
    """
    return """[
    {
        "code": "F401",
        "message": "os imported but unused",
        "filename": "test.py",
        "location": {"row": 1, "column": 1},
        "end_location": {"row": 1, "column": 10},
        "fix": {"applicability": "safe"}
    },
    {
        "code": "E501",
        "message": "Line too long (120 > 88)",
        "filename": "test.py",
        "location": {"row": 5, "column": 89},
        "end_location": {"row": 5, "column": 120},
        "fix": null
    }
]"""


@pytest.fixture
def sample_ruff_json_empty_output() -> str:
    """Provide empty JSON output from ruff check.

    Returns:
        str: Empty JSON array indicating no issues.
    """
    return "[]"


@pytest.fixture
def sample_ruff_format_check_output() -> str:
    """Provide sample output from ruff format --check.

    Returns:
        str: Sample format check output listing files to reformat.
    """
    return """Would reformat: test.py
Would reformat: src/module.py
2 files would be reformatted"""


@pytest.fixture
def sample_ruff_format_check_empty_output() -> str:
    """Provide empty output from ruff format --check.

    Returns:
        str: Empty output indicating all files properly formatted.
    """
    return ""


@pytest.fixture
def temp_python_file(tmp_path: Any) -> str:
    """Create a temporary Python file for testing.

    Args:
        tmp_path: Pytest's tmp_path fixture.

    Returns:
        str: Path to the created temporary Python file.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "python",
        "ruff",
        "ruff_e501_f401_violations.py",
        dest_name="test_file.py",
    )
    return str(test_file)


@pytest.fixture
def temp_python_files(tmp_path: Any) -> list[str]:
    """Create multiple temporary Python files for testing.

    Args:
        tmp_path: Pytest's tmp_path fixture.

    Returns:
        list[str]: Paths to the created temporary Python files.
    """
    files = []
    for i in range(3):
        test_file = copy_sample(
            tmp_path,
            "tools",
            "python",
            "common",
            "minimal_x.py",
            dest_name=f"test_file_{i}.py",
        )
        files.append(str(test_file))
    return files
