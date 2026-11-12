"""Bandit runs without a complete verdict claim no inspection (#3352).

A Bandit run that could not execute, printed no usable report, or listed
files it could not scan is an execution failure: it fails, its findings are
incomplete, the inspected set is unknown (so it can never update a severity
baseline) and nothing synthetic is added for the failure itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import (
    CompletedProcess,
)  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.tools.bandit.definition import MAX_NAMED_SCAN_ERRORS, BanditPlugin
from lintro.utils.execution.run_renderer import make_result_display
from lintro.utils.json_output import serialize_tool_result

FINDING = {
    "filename": "mod.py",
    "line_number": 1,
    "col_offset": 0,
    "issue_severity": "LOW",
    "issue_confidence": "HIGH",
    "issue_text": "Use of assert detected.",
    "test_id": "B101",
    "test_name": "assert_used",
    "more_info": "https://bandit.readthedocs.io/",
    "code": "1 assert x\n",
    "line_range": [1],
}


def _run_bandit(
    tmp_path: Path,
    *,
    stdout: str,
    returncode: int = 0,
    stderr: str = "",
) -> ToolResult:
    """Run the real plugin against a canned Bandit process.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
        stdout: Simulated Bandit stdout.
        returncode: Simulated Bandit exit code.
        stderr: Simulated Bandit stderr.

    Returns:
        The plugin's ToolResult.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("assert x\n")
    completed = CompletedProcess(
        args=["bandit"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch(
            "lintro.tools.bandit.definition.subprocess.run",
            return_value=completed,
        ),
    ):
        return BanditPlugin().check([str(py_file)], {})


@pytest.mark.parametrize(
    "errors",
    [None, "boom", {"filename": "mod.py"}, 0, True],
    ids=["null", "string", "object", "number", "boolean"],
)
@pytest.mark.parametrize("returncode", [0, 1], ids=["rc0", "rc1"])
def test_non_list_errors_is_a_malformed_report(
    tmp_path: Path,
    errors: object,
    returncode: int,
) -> None:
    """A non-list ``errors`` container fails with one parse failure, no raise.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
        errors: Malformed ``errors`` container.
        returncode: Simulated Bandit exit code.
    """
    stdout = json.dumps({"results": [FINDING], "errors": errors})

    result = _run_bandit(tmp_path, stdout=stdout, returncode=returncode)

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.inspected_paths).is_none()
    assert_that(result.issues).extracting("test_id").is_equal_to(["B101"])
    assert_that(result.output).is_equal_to(stdout)


SYNTAX_ERROR = "syntax error while parsing AST from file"


def _scan_error_report(*broken: str) -> str:
    """Build a Bandit report with one finding and unscannable files.

    Args:
        *broken: Files Bandit reports it could not scan.

    Returns:
        The report as JSON text.
    """
    return json.dumps(
        {
            "results": [FINDING],
            "errors": [{"filename": name, "reason": SYNTAX_ERROR} for name in broken],
        },
    )


def _console_text(result: ToolResult) -> str:
    """Render one result through the per-tool console callback.

    Args:
        result: Result to render.

    Returns:
        Every line the console logger received, joined by newlines.
    """
    printed: list[str] = []
    console = SimpleNamespace(
        console_output=lambda text="", **_: printed.append(text),
    )
    display = make_result_display(
        logger=console,
        output_format="grid",
        raw_output=False,
        action=Action.CHECK,
    )
    display(result)
    return "\n".join(printed)


def test_scan_errors_leave_inspection_unknown(tmp_path: Path) -> None:
    """Files Bandit could not scan keep real findings but claim no inspection.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
    """
    result = _run_bandit(tmp_path, stdout=_scan_error_report("b.py"), returncode=1)

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).is_equal_to(
        f"Bandit could not scan 1 file(s): b.py: {SYNTAX_ERROR}",
    )


def test_scan_errors_are_named_in_console_and_json(tmp_path: Path) -> None:
    """The console and JSON carry the named reason, not Bandit's raw report.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
    """
    report = _scan_error_report("b.py")
    result = _run_bandit(tmp_path, stdout=report, returncode=1)

    text = _console_text(result)
    payload = serialize_tool_result(result, action=Action.CHECK)

    assert_that(text).contains("B101", f"b.py: {SYNTAX_ERROR}")
    assert_that(text).does_not_contain('"generated_at"', '"errors"')
    assert_that(payload["execution_failed"]).is_true()
    assert_that(payload["output"]).is_equal_to(
        f"Bandit could not scan 1 file(s): b.py: {SYNTAX_ERROR}",
    )


def test_many_scan_errors_are_truncated(tmp_path: Path) -> None:
    """A long scan-error list names the first files and counts the rest.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
    """
    broken = [f"bad_{index:02d}.py" for index in range(MAX_NAMED_SCAN_ERRORS + 3)]

    result = _run_bandit(tmp_path, stdout=_scan_error_report(*broken), returncode=1)

    lines = (result.output or "").splitlines()
    assert_that(lines[0]).is_equal_to(
        f"Bandit could not scan {len(broken)} file(s):",
    )
    assert_that(lines[1:-1]).is_equal_to(
        [f"  {name}: {SYNTAX_ERROR}" for name in broken[:MAX_NAMED_SCAN_ERRORS]],
    )
    assert_that(lines[-1]).is_equal_to("  ... and 3 more")


@pytest.mark.parametrize(
    ("stdout", "returncode", "stderr", "parse_failures"),
    [
        ("", 2, "bandit: error: unrecognized arguments", None),
        (" \n", 0, "", None),
        ("this is not valid json at all", 0, "", 1),
        ('{"results": "invalid", "errors": []}', 1, "", 1),
    ],
    ids=[
        "no-output-nonzero",
        "blank-output-zero",
        "garbage",
        "invalid-results",
    ],
)
def test_unusable_report_is_an_execution_failure(
    tmp_path: Path,
    stdout: str,
    returncode: int,
    stderr: str,
    parse_failures: int | None,
) -> None:
    """No usable report means unknown inspection and no synthetic issues.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
        stdout: Simulated Bandit stdout.
        returncode: Simulated Bandit exit code.
        stderr: Simulated Bandit stderr.
        parse_failures: Expected malformed report count.
    """
    result = _run_bandit(
        tmp_path,
        stdout=stdout,
        returncode=returncode,
        stderr=stderr,
    )

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(parse_failures)
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.issues).is_empty()
    assert_that(result.output).contains(
        stderr or stdout.strip() or f"Bandit printed no report (exit {returncode}).",
    )


def test_complete_report_still_claims_inspection(tmp_path: Path) -> None:
    """A complete report with findings keeps its inspection claim.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
    """
    stdout = json.dumps({"results": [FINDING], "errors": []})

    result = _run_bandit(tmp_path, stdout=stdout, returncode=1)

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.issues).extracting("test_id").is_equal_to(["B101"])
