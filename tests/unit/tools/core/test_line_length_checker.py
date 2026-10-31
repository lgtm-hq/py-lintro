"""Unit tests for the shared line length checker utility."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.tools.core.line_length_checker import (
    LineLengthCheckUnavailableError,
    LineLengthViolation,
    RuffNotFoundError,
    check_line_length_violations,
)

if TYPE_CHECKING:
    from collections.abc import Generator


_MISSING_FIELD = object()


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


@pytest.fixture
def invalid_syntax_diagnostic() -> dict[str, object]:
    """Provide Ruff's JSON schema for a target-version syntax diagnostic.

    Returns:
        A syntax diagnostic emitted even when only E501 is selected.
    """
    return {
        "cell": None,
        "code": None,
        "end_location": {"column": 6, "row": 1},
        "filename": "/project/match.py",
        "fix": None,
        "location": {"column": 1, "row": 1},
        "message": "Cannot use `match` statement on Python 3.9",
        "name": "invalid-syntax",
        "noqa_row": None,
        "severity": "error",
        "url": None,
    }


@pytest.fixture
def recorded_ruff_argv() -> Generator[list[list[str]], None, None]:
    """Record the argv of every ``subprocess.run`` call made by the checker.

    Reading the argv back off a mock only asserts how the mock was called;
    recording it into a plain list makes the executed command an observable
    value (#2315).

    Yields:
        list[list[str]]: A list accumulating one argv per ruff invocation.
    """
    commands: list[list[str]] = []

    def fake_run(argv: list[str], *args: object, **kwargs: object) -> SimpleNamespace:
        """Record one ruff invocation and report no violations.

        Args:
            argv: Command line ruff was invoked with.
            *args: Positional arguments the caller passed through.
            **kwargs: Keyword arguments the caller passed through.

        Returns:
            A completed process reporting empty JSON output.
        """
        commands.append(list(argv))
        return SimpleNamespace(stdout="[]", returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        yield commands


# --- check_line_length_violations function tests ---


def test_check_line_length_empty_files_returns_empty_list() -> None:
    """Test that empty file list returns empty violations."""
    result = check_line_length_violations(files=[])
    assert_that(result).is_empty()


def test_check_line_length_ruff_not_available_is_explicit() -> None:
    """Distinguish an absent optional Ruff from a measured empty report."""
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(RuffNotFoundError) as exc_info,
    ):
        check_line_length_violations(files=["test.py"])
    assert_that(str(exc_info.value)).contains("Ruff not found in PATH")
    assert_that(exc_info.value.timed_out).is_false()


@pytest.mark.parametrize(
    "coordinates",
    [
        pytest.param({"location": {"row": 10, "column": 89}}, id="new"),
        pytest.param({"row": 10, "column": 89}, id="old"),
    ],
)
def test_check_line_length_successful_detection(
    mock_ruff_available: MagicMock,
    mock_subprocess: MagicMock,
    coordinates: dict[str, object],
) -> None:
    """Test successful detection of E501 violations.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        mock_subprocess: Mock fixture for subprocess operations.
        coordinates: Valid coordinates in either supported Ruff format.
    """
    ruff_output = json.dumps(
        [
            {
                "filename": "/path/to/file.py",
                **coordinates,
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


@pytest.mark.parametrize("returncode", [0, 1])
def test_check_line_length_empty_report_is_a_measurement(
    mock_ruff_available: MagicMock,
    returncode: int,
) -> None:
    """A valid empty array remains a measured zero for either normal exit code.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        returncode: A normal Ruff exit status.
    """
    process = subprocess.CompletedProcess(["ruff"], returncode, "[]", "")
    with patch("subprocess.run", return_value=process):
        violations = check_line_length_violations(files=["test.py"])
    assert_that(violations).is_empty()


@pytest.mark.parametrize("code", [None, "invalid-syntax"], ids=["null", "named"])
@pytest.mark.parametrize("include_e501", [False, True], ids=["syntax-only", "mixed"])
def test_check_line_length_skips_syntax_diagnostics(
    mock_ruff_available: MagicMock,
    invalid_syntax_diagnostic: dict[str, object],
    code: str | None,
    include_e501: bool,
) -> None:
    """Syntax reports do not erase E501 findings from other files.

    Args:
        mock_ruff_available: Mock fixture ensuring Ruff is available.
        invalid_syntax_diagnostic: A real-shaped target-version diagnostic.
        code: Ruff's old null code or newer diagnostic-name code.
        include_e501: Whether another file has a line-length violation.
    """
    invalid_syntax_diagnostic["code"] = code
    diagnostics = [invalid_syntax_diagnostic]
    if include_e501:
        diagnostics.append(
            {
                "filename": "/project/long.py",
                "code": "E501",
                "location": {"row": 2, "column": 89},
                "message": "Line too long (110 > 88)",
            },
        )
    process = subprocess.CompletedProcess(["ruff"], 1, json.dumps(diagnostics), "")
    with patch("subprocess.run", return_value=process):
        violations = check_line_length_violations(
            files=["match.py", "long.py"],
            cwd="/project",
        )

    expected = (
        [LineLengthViolation("/project/long.py", 2, 89, "Line too long (110 > 88)")]
        if include_e501
        else []
    )
    assert_that(violations).is_equal_to(expected)


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": _MISSING_FIELD},
        {"code": "E500"},
        {"code": ""},
        {"code": 501},
        {"code": False},
        {"code": []},
        {"code": {}},
        {"name": _MISSING_FIELD},
        {"name": "other"},
        {"name": None},
        {"filename": None},
        {"message": ""},
        {"location": None},
        {"location": {"row": True, "column": 1}},
        {"location": {"row": 1, "column": 0}},
    ],
)
def test_check_line_length_rejects_malformed_syntax_diagnostics(
    mock_ruff_available: MagicMock,
    invalid_syntax_diagnostic: dict[str, object],
    overrides: dict[str, object],
) -> None:
    """The syntax exception still requires valid fields and a recognized code.

    Args:
        mock_ruff_available: Mock fixture ensuring Ruff is available.
        invalid_syntax_diagnostic: Valid syntax diagnostic to corrupt.
        overrides: Malformed fields, including missing-field sentinels.
    """
    invalid_syntax_diagnostic.update(overrides)
    diagnostic = {
        key: value
        for key, value in invalid_syntax_diagnostic.items()
        if value is not _MISSING_FIELD
    }
    process = subprocess.CompletedProcess(["ruff"], 1, json.dumps([diagnostic]), "")
    with (
        patch("subprocess.run", return_value=process),
        pytest.raises(LineLengthCheckUnavailableError) as exc_info,
    ):
        check_line_length_violations(files=["match.py"], cwd="/project")

    assert_that(str(exc_info.value)).contains("Invalid Ruff JSON report")
    assert_that(exc_info.value.__cause__).is_instance_of(ValueError)


def test_check_line_length_custom_line_length(
    mock_ruff_available: MagicMock,
    recorded_ruff_argv: list[list[str]],
) -> None:
    """Pass a custom line_length through to the ruff command line.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        recorded_ruff_argv: Recorded argv of each ruff invocation.
    """
    violations = check_line_length_violations(files=["test.py"], line_length=100)

    assert_that(recorded_ruff_argv).is_length(1)
    assert_that(recorded_ruff_argv[0]).contains("--line-length", "100")
    assert_that(violations).is_empty()


@pytest.mark.parametrize(
    "exception,reason,timed_out",
    [
        (
            subprocess.TimeoutExpired(cmd=["ruff"], timeout=30),
            "timed out after 30s",
            True,
        ),
        (FileNotFoundError("ruff disappeared"), "ruff disappeared", False),
        (OSError("Permission denied"), "Permission denied", False),
        (RuntimeError("Unexpected error"), "Unexpected error", False),
    ],
    ids=["timeout", "file_not_found", "os_error", "generic_error"],
)
def test_check_line_length_execution_failure_is_unavailable(
    mock_ruff_available: MagicMock,
    exception: Exception,
    reason: str,
    timed_out: bool,
) -> None:
    """Execution errors cannot masquerade as zero E501 findings.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        exception: The exception to be raised by subprocess.run.
        reason: Visible explanation of the failure.
        timed_out: Expected timeout status.
    """
    with (
        patch("subprocess.run", side_effect=exception),
        pytest.raises(LineLengthCheckUnavailableError) as exc_info,
    ):
        check_line_length_violations(files=["test.py"], timeout=30)
    assert_that(str(exc_info.value)).contains(reason)
    assert_that(exc_info.value.timed_out).is_equal_to(timed_out)
    assert_that(isinstance(exc_info.value, RuffNotFoundError)).is_false()


@pytest.mark.parametrize(
    "stdout",
    [
        "not valid json",
        "",
        "null",
        "{}",
        '["invalid diagnostic"]',
        "[{}]",
        '[{"location": null}]',
        '[{"filename": "test.py", "message": "Line too long", "code": "E501", '
        '"location": [], "row": 1, "column": 89}]',
        '[{"filename": "test.py", "message": "Line too long", "code": "E501", '
        '"location": {}, "row": 1, "column": 89}]',
        '[{"filename": "test.py", "message": "Line too long", "code": "E501", '
        '"location": {"row": 0, "column": 89}, "row": 1, "column": 89}]',
    ],
    ids=[
        "invalid_json",
        "empty_stdout",
        "null",
        "object",
        "item",
        "empty_diagnostic",
        "location",
        "location_list",
        "missing_nested_coordinates",
        "invalid_nested_row",
    ],
)
def test_check_line_length_invalid_output_is_not_a_measurement(
    mock_ruff_available: MagicMock,
    stdout: str,
) -> None:
    """Only a valid diagnostic array can report zero findings.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        stdout: The stdout output from subprocess.run.
    """
    with (
        patch("subprocess.run", return_value=MagicMock(stdout=stdout, returncode=1)),
        pytest.raises(LineLengthCheckUnavailableError) as exc_info,
    ):
        check_line_length_violations(files=["test.py"])
    assert_that(str(exc_info.value)).contains("Invalid Ruff JSON report")
    assert_that(exc_info.value.timed_out).is_false()


@pytest.mark.parametrize("nested", [True, False], ids=["new", "old"])
@pytest.mark.parametrize(
    "field,invalid_value",
    [
        ("filename", _MISSING_FIELD),
        ("filename", None),
        ("filename", ""),
        ("filename", " \t"),
        ("filename", 123),
        ("filename", True),
        ("filename", []),
        ("filename", {}),
        ("message", _MISSING_FIELD),
        ("message", None),
        ("message", ""),
        ("message", " \t"),
        ("message", 123),
        ("message", True),
        ("message", []),
        ("message", {}),
        ("code", _MISSING_FIELD),
        ("code", None),
        ("code", ""),
        ("code", " \t"),
        ("code", 501),
        ("code", True),
        ("code", []),
        ("code", {}),
        ("code", "E500"),
        ("row", _MISSING_FIELD),
        ("row", None),
        ("row", 0),
        ("row", -1),
        ("row", 1.5),
        ("row", "1"),
        ("row", True),
        ("row", False),
        ("row", []),
        ("row", {}),
        ("column", _MISSING_FIELD),
        ("column", None),
        ("column", 0),
        ("column", -1),
        ("column", 1.5),
        ("column", "89"),
        ("column", True),
        ("column", False),
        ("column", []),
        ("column", {}),
    ],
)
def test_check_line_length_malformed_fields_are_unavailable(
    mock_ruff_available: MagicMock,
    nested: bool,
    field: str,
    invalid_value: object,
) -> None:
    """Missing or malformed fields cannot manufacture measured E501 findings.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        nested: Whether coordinates use Ruff's location wrapper.
        field: Required diagnostic field to corrupt or omit.
        invalid_value: Invalid field value, or the missing-field sentinel.
    """
    diagnostic: dict[str, object] = {
        "filename": "test.py",
        "message": "Line too long (100 > 88)",
        "code": "E501",
    }
    coordinates: dict[str, object] = {"row": 1, "column": 89}
    if nested:
        diagnostic["location"] = coordinates
    else:
        diagnostic.update(coordinates)
    fields = coordinates if nested and field in {"row", "column"} else diagnostic
    if invalid_value is _MISSING_FIELD:
        del fields[field]
    else:
        fields[field] = invalid_value
    process = subprocess.CompletedProcess(["ruff"], 1, json.dumps([diagnostic]), "")

    with (
        patch("subprocess.run", return_value=process),
        pytest.raises(LineLengthCheckUnavailableError) as exc_info,
    ):
        check_line_length_violations(files=["test.py"], cwd="/project")
    assert_that(str(exc_info.value)).contains("Invalid Ruff JSON report")
    assert_that(exc_info.value.__cause__).is_instance_of(ValueError)
    assert_that(exc_info.value.timed_out).is_false()


def test_check_line_length_failed_exit_rejects_even_empty_json(
    mock_ruff_available: MagicMock,
) -> None:
    """A Ruff execution error takes precedence over an empty report.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
    """
    process = subprocess.CompletedProcess(["ruff"], 2, "[]", "invalid configuration")
    with (
        patch("subprocess.run", return_value=process),
        pytest.raises(LineLengthCheckUnavailableError) as exc_info,
    ):
        check_line_length_violations(files=["test.py"])
    assert_that(str(exc_info.value)).contains("code 2", "invalid configuration")


def test_check_line_length_relative_paths_converted_to_absolute(
    mock_ruff_available: MagicMock,
    recorded_ruff_argv: list[list[str]],
) -> None:
    """Resolve relative file paths against ``cwd`` before invoking ruff.

    Args:
        mock_ruff_available: Mock fixture ensuring ruff is available.
        recorded_ruff_argv: Recorded argv of each ruff invocation.
    """
    violations = check_line_length_violations(
        files=["src/module.py", "tests/test_module.py"],
        cwd="/project",
    )

    assert_that(recorded_ruff_argv).is_length(1)
    assert_that(recorded_ruff_argv[0]).contains(
        "/project/src/module.py",
        "/project/tests/test_module.py",
    )
    assert_that(violations).is_empty()


def test_check_line_length_cwd_symlink_is_not_resolved(
    tmp_path: Path,
    mock_ruff_available: MagicMock,
    recorded_ruff_argv: list[list[str]],
) -> None:
    """Relative input paths preserve ``abspath`` symlink semantics.

    Args:
        tmp_path: Temporary directory fixture.
        mock_ruff_available: Mock fixture ensuring ruff is available.
        recorded_ruff_argv: Recorded argv of each ruff invocation.
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    symlink_dir = tmp_path / "link"
    symlink_dir.symlink_to(real_dir, target_is_directory=True)

    check_line_length_violations(files=["src/module.py"], cwd=str(symlink_dir))

    assert_that(recorded_ruff_argv).is_length(1)
    assert_that(recorded_ruff_argv[0]).contains(str(symlink_dir / "src" / "module.py"))
    assert_that(recorded_ruff_argv[0]).does_not_contain(
        str(real_dir / "src" / "module.py"),
    )


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
    recorded_ruff_argv: list[list[str]],
) -> None:
    """Invoke ruff with the flags the E501-only check depends on.

    Args:
        mock_ruff_available: Mock for ruff availability check.
        recorded_ruff_argv: Recorded argv of each ruff invocation.
    """
    violations = check_line_length_violations(files=["test.py"])

    assert_that(recorded_ruff_argv).is_length(1)
    assert_that(recorded_ruff_argv[0]).contains(
        "check",
        "--select",
        "E501",
        "--output-format",
        "json",
        "--no-cache",
    )
    assert_that(violations).is_empty()
