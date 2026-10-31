"""Unit tests for black plugin check/fix execution."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("code", [None, "invalid-syntax"], ids=["null", "named"])
@pytest.mark.parametrize("include_e501", [False, True], ids=["syntax-only", "mixed"])
def test_fix_formats_files_despite_ruff_invalid_syntax(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    code: str | None,
    include_e501: bool,
) -> None:
    """A Ruff target-version mismatch allows Black writes and measured E501 counts.

    Args:
        black_plugin: Plugin with the version gate bypassed.
        tmp_path: Directory for actual discovered Python files.
        code: Syntax diagnostic code in either supported Ruff schema.
        include_e501: Whether a separate file has an unfixable long line.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x=1\n")
    syntax_file = tmp_path / "match.py"
    syntax_file.write_text("match value:\n    case 1:\n        result = 1\n")
    diagnostics = [
        {
            "cell": None,
            "code": code,
            "end_location": {"column": 6, "row": 1},
            "filename": str(syntax_file),
            "fix": None,
            "location": {"column": 1, "row": 1},
            "message": "Cannot use `match` statement on Python 3.9",
            "name": "invalid-syntax",
            "noqa_row": None,
            "severity": "error",
            "url": None,
        },
    ]
    long_file = tmp_path / "long.py"
    if include_e501:
        long_file.write_text('value = "' + "x" * 100 + '"\n')
        diagnostics.append(
            {
                "filename": str(long_file),
                "code": "E501",
                "location": {"row": 1, "column": 89},
                "message": "Line too long (110 > 88)",
            },
        )
    report = subprocess.CompletedProcess(["ruff"], 1, json.dumps(diagnostics), "")

    def run_black(cmd: list[str], **kwargs: object) -> tuple[bool, str]:
        """Emulate Black checks and write formatting only on the fix invocation.

        Args:
            cmd: Black command assembled by the plugin.
            **kwargs: Subprocess settings supplied by the plugin.

        Returns:
            A formatting verdict and Black's corresponding output.
        """
        if "--check" in cmd:
            needs_format = py_file.read_text() == "x=1\n"
            return (
                not needs_format,
                f"would reformat {py_file}\n" if needs_format else "",
            )
        py_file.write_text("x = 1\n")
        return True, f"reformatted {py_file}\n"

    with (
        patch.object(black_plugin, "_run_subprocess", side_effect=run_black),
        patch("shutil.which", return_value="/mock/ruff"),
        patch("subprocess.run", return_value=report),
    ):
        result = black_plugin.fix([str(tmp_path)], {})

    assert_that(py_file.read_text()).is_equal_to("x = 1\n")
    assert_that(result.success).is_equal_to(not include_e501)
    assert_that(result.initial_issues_count).is_equal_to(1 + int(include_e501))
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(int(include_e501))
    assert_that(result.issues_count).is_equal_to(int(include_e501))
    assert_that(result.output).contains("Fixed 1 issue(s)")
    assert_that(result.output).does_not_contain("E501 supplement failed")
    assert_that(result.timed_out).is_false()
    if include_e501:
        assert result.issues is not None
        assert_that(result.issues[-1]).has_file(str(long_file)).has_code("E501")


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
        (
            subprocess.CompletedProcess(["ruff"], 1, "[{}]", ""),
            "Invalid Ruff JSON report",
            False,
        ),
    ],
    ids=["timeout", "os-error", "bad-json", "missing-fields"],
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
    assert_that(result.output).contains(
        "E501 supplement failed",
        reason,
        black_output.rstrip(),
    )
    assert_that(result.output).does_not_contain("\n\nE501")
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
        output.rstrip(),
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


@pytest.mark.parametrize(
    "process,reason,timed_out",
    [
        (subprocess.TimeoutExpired(["ruff"], 30), "timed out after 30s", True),
        (OSError("cannot execute Ruff"), "cannot execute Ruff", False),
        (FileNotFoundError("ruff disappeared"), "ruff disappeared", False),
        (
            subprocess.CompletedProcess(["ruff"], 2, "[]", "invalid configuration"),
            "invalid configuration",
            False,
        ),
        (
            subprocess.CompletedProcess(["ruff"], 0, "not json", ""),
            "Invalid Ruff JSON report",
            False,
        ),
        (
            subprocess.CompletedProcess(["ruff"], 0, "", ""),
            "Invalid Ruff JSON report",
            False,
        ),
        (
            subprocess.CompletedProcess(["ruff"], 0, "null", ""),
            "Expected a Ruff JSON array",
            False,
        ),
        (
            subprocess.CompletedProcess(["ruff"], 1, "[{}]", ""),
            "Invalid Ruff JSON report",
            False,
        ),
    ],
    ids=[
        "timeout",
        "os-error",
        "disappeared",
        "config",
        "bad-json",
        "empty",
        "null",
        "missing-fields",
    ],
)
@pytest.mark.parametrize("black_fixed", [False, True], ids=["e501-only", "black-fix"])
def test_fix_retains_e501_when_final_supplement_fails(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    process: subprocess.CompletedProcess[str] | Exception,
    reason: str,
    timed_out: bool,
    black_fixed: bool,
) -> None:
    """An unmeasured final E501 pass cannot erase or claim earlier E501 fixes.

    Args:
        black_plugin: Plugin with the version gate bypassed.
        tmp_path: Directory for a Python file with a long string.
        process: Final Ruff report or execution exception.
        reason: Expected explanation of the failed measurement.
        timed_out: Expected timeout flag.
        black_fixed: Whether Black also fixed a formatting issue.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("value = '" + "x" * 100 + "'\n")
    initial_report = json.dumps(
        [
            {
                "filename": str(py_file),
                "location": {"row": 1, "column": 89},
                "code": "E501",
                "message": "Line too long (110 > 88)",
            },
        ],
    )
    with (
        patch.object(
            black_plugin,
            "_run_subprocess",
            side_effect=[
                (not black_fixed, f"would reformat {py_file}\n" if black_fixed else ""),
                (True, f"reformatted {py_file}\n" if black_fixed else ""),
                (True, ""),
            ],
        ),
        patch("shutil.which", return_value="/mock/ruff"),
        patch(
            "subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(["ruff"], 1, initial_report, ""),
                process,
            ],
        ),
    ):
        result = black_plugin.fix([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_equal_to(timed_out)
    assert_that(result.initial_issues_count).is_equal_to(1 + int(black_fixed))
    assert_that(result.fixed_issues_count).is_equal_to(int(black_fixed))
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains(
        "E501 supplement failed",
        reason,
        "E501 findings retained from the initial check",
        "line and column positions may be stale after formatting",
    )
    if not black_fixed:
        assert_that(result.output).does_not_contain("Fixed")
    assert result.issues is not None
    retained = result.issues[-1]
    assert_that(retained).has_file(str(py_file)).has_line(1).has_column(89)
    assert_that(retained).has_code("E501").has_fixable(False)
    assert_that(retained.message).contains("Line too long")


@pytest.mark.parametrize(
    "process",
    [
        pytest.param(OSError("cannot execute Ruff"), id="os-error"),
        pytest.param(
            subprocess.CompletedProcess(["ruff"], 1, "[{}]", ""),
            id="missing-fields",
        ),
    ],
)
def test_fix_initial_supplement_failure_reports_unapplied_changes(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    process: subprocess.CompletedProcess[str] | Exception,
) -> None:
    """Aborting before formatting reports proposed changes without claiming writes.

    Args:
        black_plugin: Plugin with the version gate bypassed.
        tmp_path: Directory for a file that needs formatting.
        process: Initial Ruff report or execution exception.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x=1\n")
    with (
        patch.object(
            black_plugin,
            "_run_subprocess",
            return_value=(False, f"would reformat {py_file}\n"),
        ),
        patch("shutil.which", return_value="/mock/ruff"),
        patch("subprocess.run", side_effect=[process]),
    ):
        result = black_plugin.fix([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.output).contains(
        f"would reformat {py_file}\nE501 supplement failed",
    )
    assert_that(result.output).does_not_contain("Reformatted", "\n\nE501")
    assert result.issues is not None
    assert_that(result.issues).is_length(1)
    assert_that(result.issues[0].message).is_equal_to("Would reformat file")
    assert_that(py_file.read_text()).is_equal_to("x=1\n")


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
