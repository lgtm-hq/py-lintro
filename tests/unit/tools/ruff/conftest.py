"""Shared fixtures for ruff tool tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from lintro.enums.tool_name import ToolName
from tests.test_samples_helpers import copy_sample

if TYPE_CHECKING:
    from lintro.tools.definitions.ruff import RuffPlugin


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
