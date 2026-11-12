"""Unit tests for black plugin check/fix execution."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess results and exceptions only; no real processes
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.tools.black.definition import BlackPlugin
from lintro.utils.execution.baseline_filter import apply_baseline
from lintro.utils.execution.exit_codes import (
    aggregate_tool_results,
    determine_exit_code,
    unknown_residual_tool_names,
)
from lintro.utils.fingerprint_baseline import maintain_baseline, snapshot
from lintro.utils.meaningful_run import run_inspected_files
from tests.unit.tools.subprocess_helpers import subprocess_result

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
        patch.object(
            black_plugin,
            "_run_subprocess_result",
            return_value=subprocess_result(True, ""),
        ),
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.inspected_files).is_true()


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
            "_run_subprocess_result",
            return_value=subprocess_result(False, output),
        ),
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.inspected_files).is_true()
    issues = result.issues
    first_issue = issues[0]
    assert_that(first_issue.file).ends_with("module.py")
    assert_that(first_issue.message).contains("Would reformat")


def test_fix_retains_findings_surfaced_by_formatting(
    black_plugin: BlackPlugin,
    tmp_path: Path,
) -> None:
    """A larger post-format report retains its findings without negative fixes."""
    files = [tmp_path / name for name in ("module.py", "other.py")]
    for path in files:
        path.write_text("x=1\n")
    with (
        patch.object(
            black_plugin,
            "_run_subprocess_result",
            side_effect=[
                subprocess_result(False, f"would reformat {files[0]}\n"),
                subprocess_result(True, ""),
                subprocess_result(
                    False,
                    "".join(f"would reformat {path}\n" for path in files),
                ),
            ],
        ),
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = black_plugin.fix([str(path) for path in files], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues).is_length(1)
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_zero()
    assert_that(result.remaining_issues_count).is_equal_to(2)
    assert_that(result.issues_count).is_equal_to(2)
    assert_that(result.issues).extracting("file").is_equal_to([str(p) for p in files])
    assert_that(result.output).contains("Found 2 issue(s)").does_not_contain("Fixed")


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
            "_run_subprocess_result",
            side_effect=[
                subprocess_result(False, check_output),
                subprocess_result(True, f"reformatted {py_file}\n"),
                subprocess_result(True, ""),
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
    assert_that(result.inspected_files).is_true()


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

    def run_black(cmd: list[str], *args: object, **kwargs: object) -> SubprocessResult:
        """Emulate Black checks and write formatting only on the fix invocation.

        Args:
            cmd: Black command assembled by the plugin.
            *args: Positional subprocess settings supplied by the legacy wrapper.
            **kwargs: Subprocess settings supplied by the plugin.

        Returns:
            A formatting verdict and Black's corresponding output.
        """
        if "--check" in cmd:
            needs_format = py_file.read_text() == "x=1\n"
            return subprocess_result(
                not needs_format,
                f"would reformat {py_file}\n" if needs_format else "",
            )
        py_file.write_text("x = 1\n")
        return subprocess_result(True, f"reformatted {py_file}\n")

    with (
        patch.object(black_plugin, "_run_subprocess_result", side_effect=run_black),
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
            "_run_subprocess_result",
            return_value=subprocess_result(black_clean, black_output),
        ),
        patch("shutil.which", return_value="/mock/ruff"),
        patch("subprocess.run", side_effect=[process]),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_equal_to(timed_out)
    assert_that(result.inspected_files).is_equal_to(False if timed_out else None)
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
            "_run_subprocess_result",
            return_value=subprocess_result(black_success, output),
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
        patch.object(
            black_plugin,
            "_run_subprocess_result",
            return_value=subprocess_result(True, ""),
        ),
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
        patch.object(
            black_plugin,
            "_run_subprocess_result",
            return_value=subprocess_result(True, ""),
        ),
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
            "_run_subprocess_result",
            side_effect=[
                subprocess_result(
                    not black_fixed,
                    f"would reformat {py_file}\n" if black_fixed else "",
                ),
                subprocess_result(
                    True,
                    f"reformatted {py_file}\n" if black_fixed else "",
                ),
                subprocess_result(True, ""),
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
            "_run_subprocess_result",
            return_value=subprocess_result(False, f"would reformat {py_file}\n"),
        ),
        patch("shutil.which", return_value="/mock/ruff"),
        patch("subprocess.run", side_effect=[process]),
    ):
        result = black_plugin.fix([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.output).contains(
        f"would reformat {py_file}\nE501 supplement failed",
    )
    assert_that(result.output).does_not_contain("Reformatted", "\n\nE501")
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
            "_run_subprocess_result",
            side_effect=[
                subprocess_result(False, output),
                subprocess_result(True, ""),
                subprocess_result(not remaining, output if remaining else ""),
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


_CLEAN = (
    "example.py already well formatted, good job.\n"
    "All done! ✨ 🍰 ✨\n1 file would be left unchanged.\n"
)
_FINDING = "would reformat example.py\n1 file would be reformatted.\n"
_ERROR = (
    "error: cannot format broken.py: Cannot parse: 1:6: def f(:\n"
    "Oh no! 💥 💔 💥\n1 file would fail to reformat.\n"
)
_PARTIAL = (
    "would reformat example.py\n"
    "error: cannot format broken.py: Cannot parse: 1:6: def f(:\n"
    "Oh no! 💥 💔 💥\n"
    "1 file would be reformatted, 1 file would fail to reformat.\n"
)
_LOST = "would reformat example.py\n2 files would be reformatted.\n"
_DISTINCT = _PARTIAL.replace(
    "would reformat example.py\n",
    "would reformat example.py\nwould reformat other.py\n",
).replace("1 file would be reformatted", "2 files would be reformatted")


def _native_reports(
    monkeypatch: pytest.MonkeyPatch,
    reports: Sequence[tuple[int, str] | subprocess.TimeoutExpired],
    e501_output: str = "[]",
) -> None:
    """Replace only process execution; keep discovery, parsing and outcomes real.

    Args:
        monkeypatch: Pytest patch manager.
        reports: Consecutive Black statuses and stderr reports, or timeouts.
        e501_output: Native Ruff JSON findings for each E501 invocation.
    """
    pending = iter(reports)

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Supply native Black stderr or a legitimate empty Ruff E501 report.

        Args:
            cmd: Production subprocess command.
            **kwargs: Production subprocess settings.

        Returns:
            Native process status and separate output streams.

        Raises:
            subprocess.TimeoutExpired: For an explicitly requested timeout.
        """
        if "E501" in cmd:
            return subprocess.CompletedProcess(cmd, 0, e501_output, "")
        report = next(pending)
        if isinstance(report, subprocess.TimeoutExpired):
            raise subprocess.TimeoutExpired(cmd, report.timeout)
        return subprocess.CompletedProcess(cmd, report[0], "", report[1])

    monkeypatch.setattr("subprocess.run", run)


def _assert_failed_fix_baseline_preserved(
    result: ToolResult,
    black_plugin: BlackPlugin,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incomplete format results cannot suppress findings or prune prior entries."""
    _native_reports(monkeypatch, [(1, _FINDING)])
    baseline = snapshot([black_plugin.check([str(root / "example.py")], {})], root=root)
    assert_that(baseline.entries).is_length(1)
    assert_that(snapshot([result], root=root).entries).is_empty()
    maintained = maintain_baseline(
        baseline,
        snapshot([result], root=root),
        results=[result],
        root=root,
        prune=True,
    )
    assert_that(maintained.entries).is_equal_to(baseline.entries)
    artifact = RunArtifact(
        tool_results=[result],
        action=Action.FIX,
        workspace_root=root,
        total_issues=result.issues_count,
        exit_code=1,
    )
    apply_baseline(artifact, baseline)
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(artifact.total_issues).is_equal_to(result.issues_count)
    assert_that(artifact.tool_results[0].baselined_count).is_zero()


@pytest.mark.parametrize(
    ("native", "inspected", "count", "parse_failures", "failed"),
    [
        ((0, _CLEAN), True, 0, None, False),
        ((1, _FINDING), True, 1, None, False),
        ((123, _ERROR), None, 0, None, True),
        ((123, _PARTIAL), None, 1, None, True),
        ((1, _LOST), None, 1, 1, False),
        ((0, _LOST), None, 1, 1, False),
        ((1, "1 file would be reformatted.\n"), None, 1, 1, False),
        ((2, "error: invalid configuration\n"), None, 0, None, True),
    ],
    ids=[
        "clean",
        "finding",
        "error",
        "partial",
        "lost",
        "lost-zero",
        "summary",
        "config",
    ],
)
def test_check_inspection_requires_complete_native_verdict(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native: tuple[int, str],
    inspected: bool | None,
    count: int,
    parse_failures: int | None,
    failed: bool,
) -> None:
    """Failed or partially parsed reports retain findings without coverage claims."""
    returncode, report = native
    file = tmp_path / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    black_plugin.set_options(_baseline_coverage=True)
    _native_reports(monkeypatch, [(1, _FINDING), (returncode, report)])
    baseline = snapshot([black_plugin.check([str(file)], {})], root=tmp_path)
    assert_that(baseline.entries).is_length(1)
    if report == _CLEAN:
        file.write_text("x = 1\n", encoding="utf-8")
    if report in {_ERROR, _PARTIAL}:
        (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    if report == _LOST:
        (tmp_path / "other.py").write_text("y=2\n", encoding="utf-8")

    result = black_plugin.check([str(tmp_path)], {})

    assert_that(result.inspected_files).is_equal_to(inspected)
    assert_that(run_inspected_files([result])).is_equal_to(inspected is True)
    assert_that(result.success).is_equal_to(report == _CLEAN)
    assert_that(result.findings_complete).is_equal_to(inspected is True)
    assert_that(result.execution_failed).is_equal_to(failed)
    assert_that(result.parse_failures_count).is_equal_to(parse_failures)
    assert_that(result.issues_count).is_equal_to(count)
    if inspected is None:
        assert_that(result.inspected_paths).is_none()
        assert_that(result.output).contains(report.rstrip())
        assert_that(snapshot([result], root=tmp_path).entries).is_empty()
        maintained = maintain_baseline(
            baseline,
            snapshot([result], root=tmp_path),
            results=[result],
            root=tmp_path,
            prune=True,
        )
        assert_that(maintained.entries).is_equal_to(baseline.entries)
    artifact = RunArtifact(
        tool_results=[result],
        workspace_root=tmp_path,
        total_issues=count,
        exit_code=int(not result.success),
    )
    apply_baseline(artifact, baseline)
    assert_that(artifact.exit_code).is_equal_to(0 if inspected else 1)
    assert_that(artifact.tool_results[0].baselined_count).is_equal_to(
        1 if report == _FINDING else 0,
    )


@pytest.mark.parametrize("stage", ["initial", "mutation", "final", "diff"])
@pytest.mark.parametrize(
    ("returncode", "report", "parse_failures", "reported_files"),
    [
        (123, _ERROR, None, []),
        (123, _PARTIAL, None, ["example.py"]),
        (123, _DISTINCT, None, ["example.py", "other.py"]),
        (1, _LOST, 1, ["example.py"]),
        (0, _LOST, 1, ["example.py"]),
        (1, "1 file would be reformatted.\n", 1, ["<unknown>"]),
        (2, "error: invalid configuration\n", None, []),
        (1, "", None, []),
    ],
    ids=[
        "error",
        "partial",
        "distinct",
        "lost",
        "lost-zero",
        "summary",
        "config",
        "empty",
    ],
)
def test_fix_incomplete_pass_cannot_claim_inspection_or_measured_fixes(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    returncode: int,
    report: str,
    parse_failures: int | None,
    reported_files: list[str],
) -> None:
    """An incomplete Black pass cannot become a successful measured formatter run."""
    file = tmp_path / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    expected_files = reported_files
    if stage == "mutation":
        expected_files = ["example.py"]
    elif stage == "final":
        # A summary-only entry may be the retained file, so it adds nothing.
        named = [name for name in expected_files if name != "<unknown>"]
        expected_files = list(dict.fromkeys(["example.py", *named]))
    if returncode == 123:
        (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    if stage == "mutation" or report in {_LOST, _DISTINCT}:
        (tmp_path / "other.py").write_text("y=2\n", encoding="utf-8")
    if stage == "mutation":
        # A distinct applied file must never replace the initial open finding.
        if report != _DISTINCT:
            report = report.replace("example.py", "other.py")
        report = report.replace("would reformat ", "reformatted ")
        report = report.replace("would be reformatted", "reformatted")
        report = report.replace("would fail to reformat", "failed to reformat")
    reports = [(1, _FINDING), (0, "reformatted example.py\n"), (0, _CLEAN)]
    if stage == "diff":
        black_plugin.set_options(diff=True)
        reports = [(returncode, report), (0, _CLEAN)]
    else:
        reports[["initial", "mutation", "final"].index(stage)] = (returncode, report)
    _native_reports(monkeypatch, reports)

    result = black_plugin.fix([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_paths).is_none()
    assert_that(result.execution_failed).is_equal_to(
        returncode not in {0, 1}
        or (returncode == 1 and (not report or stage in {"mutation", "diff"})),
    )
    assert_that(result.parse_failures_count).is_equal_to(parse_failures)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(
        None if stage in {"initial", "diff"} else 1,
    )
    if report:
        assert_that(result.output).is_equal_to(report)
    else:
        assert_that(result.output).contains("exit code 1", "No stdout/stderr")
    totals = aggregate_tool_results([result], Action.FIX)
    assert_that(totals).is_equal_to((len(expected_files), 0, 0))
    assert_that(result.issues).extracting("file").is_equal_to(expected_files)
    assert_that(result.issues).extracting("message").does_not_contain(
        "Reformatted file",
    )
    assert_that(unknown_residual_tool_names([result])).is_equal_to(["black"])
    assert_that(
        determine_exit_code(Action.FIX, [result], totals[0], totals[2]),
    ).is_equal_to(1)
    _assert_failed_fix_baseline_preserved(result, black_plugin, tmp_path, monkeypatch)
    assert_that(file.read_text()).is_equal_to("x=1\n")


@pytest.mark.parametrize("stage", ["mutation", "final"])
def test_incomplete_black_retains_each_initial_e501_finding(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    """File-level formatting deduplication preserves distinct initial E501s."""
    file = tmp_path / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("y=2\n", encoding="utf-8")
    e501_output = json.dumps(
        [
            {
                "filename": "example.py",
                "code": "E501",
                "location": {"row": line, "column": 89},
                "message": "Line too long (100 > 88)",
            }
            for line in (1, 2)
        ],
    )
    reports = [(1, _FINDING)]
    if stage == "final":
        reports.append((0, "reformatted example.py\n"))
    reports.append(
        (123, _PARTIAL if stage == "final" else "reformatted other.py\n" + _ERROR),
    )
    _native_reports(monkeypatch, reports, e501_output)

    result = black_plugin.fix([str(tmp_path)], {})

    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((3, 0, 0))
    assert_that(result.issues).extracting("file", "code", "line").is_equal_to(
        [
            ("example.py", "", 0),
            (str(file), "E501", 1),
            (str(file), "E501", 2),
        ],
    )
    assert_that(result.initial_issues).is_equal_to(result.issues)
    assert_that(result.initial_issues_count).is_equal_to(3)
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()


@pytest.mark.parametrize("diff", [False, True], ids=["format", "diff"])
@pytest.mark.parametrize("remaining", [False, True], ids=["clean", "finding"])
def test_fix_complete_reports_keep_aggregate_accounting(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    diff: bool,
    remaining: bool,
) -> None:
    """Complete format and safe diff reports retain measured consumer totals."""
    file = tmp_path / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    black_plugin.set_options(diff=diff)
    reports = [] if diff else [(1, _FINDING)]
    reports.extend(
        [
            (0, _FINDING if diff else "reformatted example.py\n1 file reformatted.\n"),
            (1, _FINDING) if remaining else (0, _CLEAN),
        ],
    )
    _native_reports(monkeypatch, reports)

    result = black_plugin.fix([str(file)], {})

    assert_that(result.inspected_files).is_true()
    assert_that(result.success).is_equal_to(not remaining)
    assert_that(result.execution_failed).is_false()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.residual_unknown).is_false()
    expected_fixed = int(not diff and not remaining)
    assert_that(result.fixed_issues_count).is_equal_to(expected_fixed)
    assert_that(result.remaining_issues_count).is_equal_to(int(remaining))
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to(
        (int(remaining), expected_fixed, int(remaining)),
    )
    assert_that(unknown_residual_tool_names([result])).is_empty()
    assert_that(file.read_text()).is_equal_to("x=1\n")


@pytest.mark.parametrize("fix", [False, True], ids=["check", "fix"])
@pytest.mark.parametrize("timeout", [False, True], ids=["no-files", "timeout"])
def test_inactive_black_does_not_claim_inspection(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fix: bool,
    timeout: bool,
) -> None:
    """Empty discovery succeeds without inspection; native timeouts stay failures."""
    if timeout:
        (tmp_path / "example.py").write_text("x=1\n", encoding="utf-8")
    _native_reports(monkeypatch, [subprocess.TimeoutExpired(["black"], 30)])

    result = (black_plugin.fix if fix else black_plugin.check)([str(tmp_path)], {})

    assert_that(result.inspected_files).is_false()
    assert_that(result.success).is_equal_to(not timeout)
    assert_that(result.no_files).is_equal_to(not timeout)
    assert_that(result.timed_out).is_equal_to(timeout)
    assert_that(result.issues).is_empty()
    assert_that(result.findings_complete).is_false()


@pytest.mark.parametrize("stage", ["mutation", "final"])
def test_fix_timeout_preserves_only_completed_initial_measurement(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    """A later timeout cannot credit fixes or claim a complete residual count."""
    file = tmp_path / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    reports: list[tuple[int, str] | subprocess.TimeoutExpired] = [(1, _FINDING)]
    if stage == "final":
        reports.append((0, "reformatted example.py\n"))
    reports.append(subprocess.TimeoutExpired(["black"], 30))
    _native_reports(monkeypatch, reports)

    result = black_plugin.fix([str(file)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((1, 0, 0))
    assert_that(result.issues).extracting("file").is_equal_to(["example.py"])


def test_final_summary_counts_only_files_beyond_retained_findings(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A summary-only final report adds only what exceeds the retained files."""
    file = tmp_path / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("y=2\n", encoding="utf-8")
    _native_reports(
        monkeypatch,
        [
            (1, _FINDING),
            (0, "reformatted example.py\n"),
            (1, "2 files would be reformatted.\n"),
        ],
    )

    result = black_plugin.fix([str(tmp_path)], {})

    assert_that(result.issues).extracting("file").is_equal_to(
        ["example.py", "<unknown>"],
    )
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((2, 0, 0))
    assert_that(result.residual_unknown).is_true()


@pytest.mark.parametrize(
    "final",
    [
        pytest.param(subprocess.TimeoutExpired(["black"], 30), id="timeout"),
        pytest.param((123, _ERROR), id="error"),
    ],
)
def test_fix_diff_keeps_completed_preview_when_final_check_fails(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    final: tuple[int, str] | subprocess.TimeoutExpired,
) -> None:
    """A failed final check cannot drop findings from a completed diff preview."""
    file = tmp_path / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    black_plugin.set_options(diff=True)
    _native_reports(monkeypatch, [(0, _FINDING), final])

    result = black_plugin.fix([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_equal_to(not isinstance(final, tuple))
    assert_that(result.issues).extracting("file", "message").is_equal_to(
        [("example.py", "Would reformat file")],
    )
    assert_that(result.initial_issues_count).is_none()
    assert_that(aggregate_tool_results([result], Action.FIX)).is_equal_to((1, 0, 0))
    assert_that(file.read_text()).is_equal_to("x=1\n")


@pytest.mark.parametrize("fix", [False, True], ids=["check", "fix"])
def test_silent_black_exit_names_quiet_setting(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fix: bool,
) -> None:
    """Exit 1 with no report fails the run and explains Black's quiet setting."""
    file = tmp_path / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    _native_reports(monkeypatch, [(1, "")])

    result = (black_plugin.fix if fix else black_plugin.check)([str(file)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues).is_empty()
    assert_that(result.output).contains("No stdout/stderr", "`quiet = true`")
    assert_that(file.read_text()).is_equal_to("x=1\n")


@pytest.mark.parametrize("separator", ["\n", "\r"], ids=["lf", "cr"])
def test_check_split_filename_is_a_parse_failure(
    black_plugin: BlackPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    separator: str,
) -> None:
    """A filename split by CR or LF cannot become a silently wrong finding."""
    name = f"a{separator}b.py"
    file = tmp_path / name
    file.write_text("x=1\n", encoding="utf-8")
    _native_reports(
        monkeypatch,
        [(1, f"would reformat {name}\n1 file would be reformatted.\n")],
    )

    result = black_plugin.check([str(file)], {})

    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.findings_complete).is_false()
    assert_that(result.success).is_false()
