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
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.bandit.definition import BanditPlugin

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


def test_scan_errors_leave_inspection_unknown(tmp_path: Path) -> None:
    """Files Bandit could not scan keep real findings but claim no inspection.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
    """
    stdout = json.dumps(
        {
            "results": [FINDING],
            "errors": [{"filename": "broken.py", "reason": "syntax error"}],
        },
    )

    result = _run_bandit(tmp_path, stdout=stdout, returncode=1)

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains("broken.py")


@pytest.mark.parametrize(
    ("stdout", "returncode", "stderr", "parse_failures"),
    [
        ("", 2, "bandit: error: unrecognized arguments", None),
        ("this is not valid json at all", 0, "", 1),
        ('{"results": "invalid", "errors": []}', 1, "", 1),
    ],
    ids=["no-output-nonzero", "garbage", "invalid-results"],
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
    assert_that(result.output).contains(stderr or stdout)


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
