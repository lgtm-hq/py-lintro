"""Unit tests for black plugin check/fix execution."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess results and exceptions only; no real processes
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.black.definition import BlackPlugin

# Tests for BlackPlugin.check method


def test_check_clean_file(black_plugin: BlackPlugin, tmp_path: Path) -> None:
    """Check reports success when file is already formatted.

    Args:
        black_plugin: The BlackPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text('"""Module."""\n')

    with (
        patch.object(black_plugin, "_run_subprocess", return_value=(True, "")),
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_needs_reformat(black_plugin: BlackPlugin, tmp_path: Path) -> None:
    """Check reports issues when black would reformat the file.

    Args:
        black_plugin: The BlackPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x=1\n")

    output = f"would reformat {py_file}\n"

    with (
        patch.object(
            black_plugin,
            "_run_subprocess",
            return_value=(False, output),
        ),
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    issues = result.issues
    assert issues is not None  # narrow type for mypy
    first_issue = issues[0]
    assert_that(first_issue.file).ends_with("module.py")
    assert_that(first_issue.message).contains("Would reformat")


def test_fix_reformats_and_rechecks(
    black_plugin: BlackPlugin,
    tmp_path: Path,
) -> None:
    """Fix applies formatting and reports the initial/fixed/remaining counts.

    Args:
        black_plugin: The BlackPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x=1\n")
    check_output = f"would reformat {py_file}\n"

    with (
        patch.object(
            black_plugin,
            "_run_subprocess",
            side_effect=[
                (False, check_output),
                (True, f"reformatted {py_file}\n"),
                (True, ""),
            ],
        ),
        patch.object(
            black_plugin,
            "_check_line_length_violations",
            return_value=[],
        ),
    ):
        result = black_plugin.fix([str(py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)


@pytest.mark.parametrize(
    "process,reason,timed_out",
    [
        (subprocess.TimeoutExpired(["ruff"], 30), "timed out after 30s", True),
        (OSError("cannot execute Ruff"), "cannot execute Ruff", False),
        (
            subprocess.CompletedProcess(["ruff"], 0, "not json", ""),
            "Invalid Ruff JSON report",
            False,
        ),
    ],
    ids=["timeout", "os-error", "bad-json"],
)
@pytest.mark.parametrize("black_clean", [True, False], ids=["clean", "findings"])
def test_check_surfaces_unmeasured_supplement(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    process: subprocess.CompletedProcess[str] | Exception,
    reason: str,
    timed_out: bool,
    black_clean: bool,
) -> None:
    """A failed E501 pass fails Black while retaining its formatting findings.

    Args:
        black_plugin: Plugin with the version gate bypassed.
        tmp_path: Directory for a real discovered Python file.
        process: Ruff report or execution exception.
        reason: Failure explanation that must reach the result.
        timed_out: Expected timeout flag.
        black_clean: Whether Black itself found formatting issues.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x = 1\n")
    black_output = "" if black_clean else f"would reformat {py_file}\n"
    with (
        patch.object(
            black_plugin,
            "_run_subprocess",
            return_value=(black_clean, black_output),
        ),
        patch("shutil.which", return_value="/mock/ruff"),
        patch("subprocess.run", side_effect=[process]),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_equal_to(timed_out)
    assert_that(result.output).contains("E501 supplement failed", reason, black_output)
    assert_that(result.issues_count).is_equal_to(0 if black_clean else 1)
    assert_that(result.skipped).is_false()


@pytest.mark.parametrize(
    "black_success,black_output,expected_count",
    [
        (True, "", 0),
        (False, "would reformat {file}\n", 1),
        (False, "error: invalid configuration\n", 0),
    ],
    ids=["clean", "findings", "black-error"],
)
def test_check_missing_ruff_preserves_black_verdict(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    black_success: bool,
    black_output: str,
    expected_count: int,
) -> None:
    """Missing Ruff leaves Black's own verdict intact with a visible skip note.

    Args:
        black_plugin: Plugin with the version gate bypassed.
        tmp_path: Directory for a real discovered Python file.
        black_success: Black's subprocess verdict.
        black_output: Black's report template.
        expected_count: Number of formatting findings.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x = 1\n")
    output = black_output.format(file=py_file)
    with (
        patch.object(
            black_plugin,
            "_run_subprocess",
            return_value=(black_success, output),
        ),
        patch("shutil.which", return_value=None),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_equal_to(black_success)
    assert_that(result.issues_count).is_equal_to(expected_count)
    assert_that(result.output).contains(
        "E501 supplement skipped",
        "Ruff not found",
        output,
    )
    assert_that(result.timed_out).is_false()
    assert_that(result.skipped).is_false()


def test_check_valid_empty_supplement_is_clean(
    black_plugin: BlackPlugin,
    tmp_path: Path,
) -> None:
    """A measured empty E501 array needs no failure or skip explanation.

    Args:
        black_plugin: Plugin with the version gate bypassed.
        tmp_path: Directory for a real discovered Python file.
    """
    py_file = tmp_path / "clean.py"
    py_file.write_text("x = 1\n")
    with (
        patch.object(black_plugin, "_run_subprocess", return_value=(True, "")),
        patch("shutil.which", return_value="/mock/ruff"),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(["ruff"], 0, "[]", ""),
        ),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).is_none()
    assert_that(result.issues).is_empty()
    assert_that(result.timed_out).is_false()


@pytest.mark.parametrize("stage", ["initial", "final", "diff"])
def test_fix_retains_supplement_timeout(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    stage: str,
) -> None:
    """An unmeasured supplement fails formatting at any invocation.

    Args:
        black_plugin: Plugin with the version gate bypassed.
        tmp_path: Directory for a real discovered Python file.
        stage: Supplement invocation that exceeds its deadline.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x = 1\n")
    clean = subprocess.CompletedProcess(["ruff"], 0, "[]", "")
    timeout = subprocess.TimeoutExpired(["ruff"], 30)
    reports = {
        "initial": [timeout, clean],
        "final": [clean, timeout],
        "diff": [timeout],
    }
    black_plugin.set_options(diff=stage == "diff")
    with (
        patch.object(black_plugin, "_run_subprocess", return_value=(True, "")),
        patch("shutil.which", return_value="/mock/ruff"),
        patch("subprocess.run", side_effect=reports[stage]),
    ):
        result = black_plugin.fix([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.output).contains("E501 supplement failed", "timed out after 30s")
    assert_that(result.issues_count).is_zero()
    assert_that(result.fixed_issues_count).is_equal_to(
        None if stage == "initial" else 0,
    )


@pytest.mark.parametrize("remaining", [True, False], ids=["unfixed", "fixed"])
def test_fix_missing_ruff_keeps_formatting_result(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    remaining: bool,
) -> None:
    """A skipped supplement preserves fix counts and adds a single skip note.

    Args:
        black_plugin: Plugin with the version gate bypassed.
        tmp_path: Directory for a real discovered Python file.
        remaining: Whether Black still reports a formatting issue.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x=1\n")
    output = f"would reformat {py_file}\n"
    with (
        patch.object(
            black_plugin,
            "_run_subprocess",
            side_effect=[
                (False, output),
                (True, ""),
                (not remaining, output if remaining else ""),
            ],
        ),
        patch("shutil.which", return_value=None),
    ):
        result = black_plugin.fix([str(py_file)], {})

    assert_that(result.success).is_equal_to(not remaining)
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(0 if remaining else 1)
    assert_that(result.remaining_issues_count).is_equal_to(1 if remaining else 0)
    assert_that(result.output).contains("E501 supplement skipped", "Ruff not found")
    assert_that(str(result.output).count("E501 supplement skipped")).is_equal_to(1)
    assert_that(result.timed_out).is_false()
