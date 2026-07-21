"""Unit tests for the shared line length checker utility."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.tools.core.line_length_checker import (
    LineLengthViolation,
    check_line_length_violations,
)

if TYPE_CHECKING:
    from collections.abc import Generator


# --- LineLengthViolation dataclass tests ---


def test_line_length_violation_default_code_is_e501() -> None:
    """Test that the default code is E501."""
    violation = LineLengthViolation(
        file="test.py",
        line=10,
        column=89,
        message="Line too long (100 > 88)",
    )
    assert_that(violation.code).is_equal_to("E501")


def test_line_length_violation_all_fields_set() -> None:
    """Test creating a violation with all fields."""
    violation = LineLengthViolation(
        file="/path/to/file.py",
        line=42,
        column=100,
        message="Line too long (120 > 88)",
        code="E501",
    )
    assert_that(violation.file).is_equal_to("/path/to/file.py")
    assert_that(violation.line).is_equal_to(42)
    assert_that(violation.column).is_equal_to(100)
    assert_that(violation.message).is_equal_to("Line too long (120 > 88)")
    assert_that(violation.code).is_equal_to("E501")


# --- Fixtures for check_line_length_violations tests ---


@pytest.fixture
def mock_ruff_available() -> Generator[MagicMock, None, None]:
    """Mock shutil.which to return ruff as available.

    Yields:
        MagicMock: Configured mock for shutil.which.
    """
    with patch("shutil.which", return_value="/usr/bin/ruff") as mock:
        yield mock


@pytest.fixture
def mock_subprocess() -> Generator[MagicMock, None, None]:
    """Mock subprocess.run for testing.

    Yields:
        MagicMock: Configured mock for subprocess.run.
    """
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(stdout="[]", returncode=0)
        yield mock


# --- check_line_length_violations function tests ---


def test_check_line_length_empty_files_returns_empty_list() -> None:
    """Test that empty file list returns empty violations."""
    result = check_line_length_violations(files=[])
    assert_that(result).is_empty()


def test_check_line_length_ruff_not_available_returns_empty() -> None:
    """Test that missing ruff returns empty violations without error."""
    with patch("shutil.which", return_value=None) as mock_which:
        result = check_line_length_violations(files=["test.py"])
        assert_that(result).is_empty()
        mock_which.assert_called_once_with("ruff")


def test_check_line_length_successful_detection(
    mock_ruff_available: MagicMock,
    mock_subprocess: MagicMock,
) -> None:
    """Test successful detection of E501 violations.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        mock_subprocess: Mock fixture for subprocess operations.
    """
    ruff_output = json.dumps(
        [
            {
                "filename": "/path/to/file.py",
                "location": {"row": 10, "column": 89},
                "message": "Line too long (100 > 88)",
                "code": "E501",
            },
        ],
    )
    mock_subprocess.return_value = MagicMock(stdout=ruff_output, returncode=1)

    result = check_line_length_violations(files=["file.py"], cwd="/path/to")

    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("/path/to/file.py")
    assert_that(result[0].line).is_equal_to(10)
    assert_that(result[0].column).is_equal_to(89)
    assert_that(result[0].message).is_equal_to("Line too long (100 > 88)")
    assert_that(result[0].code).is_equal_to("E501")


def test_check_line_length_custom_line_length(
    mock_ruff_available: MagicMock,
    mock_subprocess: MagicMock,
) -> None:
    """Test that custom line_length is passed to ruff.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        mock_subprocess: Mock fixture for subprocess operations.
    """
    check_line_length_violations(files=["test.py"], line_length=100)

    call_args = mock_subprocess.call_args
    cmd = call_args[0][0]
    assert_that(cmd).contains("--line-length")
    assert_that(cmd).contains("100")


@pytest.mark.parametrize(
    "exception,description",
    [
        (subprocess.TimeoutExpired(cmd=["ruff"], timeout=30), "timeout"),
        (FileNotFoundError("ruff not found"), "file_not_found"),
        (RuntimeError("Unexpected error"), "generic_error"),
    ],
    ids=["timeout", "file_not_found", "generic_error"],
)
def test_check_line_length_exception_returns_empty(
    mock_ruff_available: MagicMock,
    exception: Exception,
    description: str,
) -> None:
    """Test that various exceptions return empty list gracefully.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        exception: The exception to be raised by subprocess.run.
        description: Description of the test case.
    """
    with patch("subprocess.run", side_effect=exception):
        result = check_line_length_violations(files=["test.py"], timeout=30)
        assert_that(result).is_empty()


@pytest.mark.parametrize(
    "stdout,description",
    [
        ("not valid json", "invalid_json"),
        ("", "empty_stdout"),
    ],
    ids=["invalid_json", "empty_stdout"],
)
def test_check_line_length_invalid_output_returns_empty(
    mock_ruff_available: MagicMock,
    stdout: str,
    description: str,
) -> None:
    """Test that invalid/empty stdout returns empty list gracefully.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        stdout: The stdout output from subprocess.run.
        description: Description of the test case.
    """
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=stdout, returncode=1)
        result = check_line_length_violations(files=["test.py"])
        assert_that(result).is_empty()


def test_check_line_length_relative_paths_converted_to_absolute(
    mock_ruff_available: MagicMock,
    mock_subprocess: MagicMock,
) -> None:
    """Test that relative file paths are converted to absolute.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        mock_subprocess: Mock fixture for subprocess operations.
    """
    check_line_length_violations(
        files=["src/module.py", "tests/test_module.py"],
        cwd="/project",
    )

    call_args = mock_subprocess.call_args
    cmd = call_args[0][0]
    assert_that(cmd).contains("/project/src/module.py")
    assert_that(cmd).contains("/project/tests/test_module.py")


def test_check_line_length_cwd_symlink_is_not_resolved(
    tmp_path: Path,
    mock_ruff_available: MagicMock,
    mock_subprocess: MagicMock,
) -> None:
    """Relative input paths preserve ``abspath`` symlink semantics.

    Args:
        tmp_path: Temporary directory fixture.
        mock_ruff_available: Mock fixture ensuring ruff is available.
        mock_subprocess: Mock fixture for subprocess operations.
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    symlink_dir = tmp_path / "link"
    symlink_dir.symlink_to(real_dir, target_is_directory=True)

    check_line_length_violations(files=["src/module.py"], cwd=str(symlink_dir))

    cmd = mock_subprocess.call_args[0][0]
    assert_that(cmd).contains(str(symlink_dir / "src" / "module.py"))
    assert_that(cmd).does_not_contain(str(real_dir / "src" / "module.py"))


def test_check_line_length_old_ruff_json_format(
    mock_ruff_available: MagicMock,
    mock_subprocess: MagicMock,
) -> None:
    """Test compatibility with older Ruff JSON format (no location wrapper).

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        mock_subprocess: Mock fixture for subprocess operations.
    """
    ruff_output = json.dumps(
        [
            {
                "filename": "/path/to/file.py",
                "row": 15,
                "column": 100,
                "message": "Line too long (110 > 88)",
                "code": "E501",
            },
        ],
    )
    mock_subprocess.return_value = MagicMock(stdout=ruff_output, returncode=1)

    result = check_line_length_violations(files=["file.py"], cwd="/path/to")

    assert_that(result).is_length(1)
    assert_that(result[0].line).is_equal_to(15)
    assert_that(result[0].column).is_equal_to(100)


def test_check_line_length_multiple_violations(
    mock_ruff_available: MagicMock,
    mock_subprocess: MagicMock,
) -> None:
    """Test handling multiple E501 violations.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        mock_subprocess: Mock fixture for subprocess operations.
    """
    ruff_output = json.dumps(
        [
            {
                "filename": "/path/file1.py",
                "location": {"row": 10, "column": 89},
                "message": "Line too long (100 > 88)",
                "code": "E501",
            },
            {
                "filename": "/path/file2.py",
                "location": {"row": 25, "column": 89},
                "message": "Line too long (150 > 88)",
                "code": "E501",
            },
            {
                "filename": "/path/file1.py",
                "location": {"row": 50, "column": 89},
                "message": "Line too long (200 > 88)",
                "code": "E501",
            },
        ],
    )
    mock_subprocess.return_value = MagicMock(stdout=ruff_output, returncode=1)

    result = check_line_length_violations(files=["file1.py", "file2.py"])

    assert_that(result).is_length(3)
    assert_that([v.file for v in result]).contains("/path/file1.py", "/path/file2.py")


def test_check_line_length_command_includes_required_flags(
    mock_ruff_available: MagicMock,
    mock_subprocess: MagicMock,
) -> None:
    """Test that the ruff command includes required flags.

    Args:
        mock_ruff_available: Mock for ruff availability check.
        mock_subprocess: Mock for subprocess calls.
    """
    check_line_length_violations(files=["test.py"])

    call_args = mock_subprocess.call_args
    cmd = call_args[0][0]

    assert_that(cmd).contains("check")
    assert_that(cmd).contains("--select")
    assert_that(cmd).contains("E501")
    assert_that(cmd).contains("--output-format")
    assert_that(cmd).contains("json")
    assert_that(cmd).contains("--no-cache")
