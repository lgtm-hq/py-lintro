"""A Ruff fix run that never ran is an execution failure (#3388).

With a ``pyproject.toml`` Ruff cannot parse, every Ruff command exits 2 with
empty stdout and ``ruff failed`` on stderr. ``lintro fmt`` must not read that
as "No fixes applied." over inspected files: the run fails as an execution
error with unknown inspection, quotes Ruff's message, and keeps only the
findings an earlier stage really reported.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - builds canned process results only
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.result_completeness import is_incomplete_result
from lintro.models.core.tool_result import ToolResult

if TYPE_CHECKING:
    from lintro.tools.ruff.definition import RuffPlugin

BROKEN_CONFIG = subprocess.CompletedProcess(
    ["ruff"],
    2,
    "",
    "ruff failed\n"
    "  Cause: Failed to parse /project/pyproject.toml\n"
    "  Cause: TOML parse error at line 1, column 11\n",
)
FINDING = {
    "filename": "test_file.py",
    "message": "`os` imported but unused",
    "code": "F401",
    "location": {"row": 1, "column": 8},
}


def _clean(stdout: str = "[]") -> subprocess.CompletedProcess[str]:
    """Build one Ruff command that completed.

    Args:
        stdout: What the command printed.

    Returns:
        A completed process exiting 0.
    """
    return subprocess.CompletedProcess(["ruff"], 0, stdout, "")


def _fix(
    ruff_plugin: RuffPlugin,
    path: str,
    processes: list[subprocess.CompletedProcess[str]],
    *,
    format_files: bool,
) -> ToolResult:
    """Run the real fix path against canned Ruff processes.

    Args:
        ruff_plugin: Plugin under test.
        path: File to fix.
        processes: One completed process per Ruff command, in order.
        format_files: Whether ``ruff format`` stages run.

    Returns:
        The plugin's ToolResult.
    """
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch("subprocess.run", side_effect=processes),
    ):
        return ruff_plugin.fix([path], {"format": format_files})


@pytest.mark.parametrize(
    ("stage", "earlier", "format_files"),
    [
        ("ruff check", [], False),
        ("ruff check", [], True),
        ("ruff format --check", ["[]"], True),
        ("ruff check --fix", ["[]"], False),
    ],
    ids=["initial-check", "initial-check-with-format", "format-check", "lint-fix"],
)
def test_broken_config_is_an_execution_failure(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    stage: str,
    earlier: list[str],
    format_files: bool,
) -> None:
    """Whichever stage Ruff fails at, the run claims nothing it did not do."""
    # Stages before the failing one complete with a clean report.
    processes = [_clean(stdout) for stdout in earlier]
    result = _fix(
        ruff_plugin,
        temp_python_file,
        [*processes, BROKEN_CONFIG],
        format_files=format_files,
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(is_incomplete_result(result)).is_true()
    assert_that(result.issues).is_empty()
    assert_that(result.output).starts_with(
        f"Ruff could not run ({stage} printed no report): ruff failed",
    ).contains("Failed to parse /project/pyproject.toml").does_not_contain(
        "No fixes applied",
    )


def test_findings_before_the_failure_are_kept(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
) -> None:
    """A finding the pre-fix check reported survives a failed fix stage."""
    result = _fix(
        ruff_plugin,
        temp_python_file,
        [
            subprocess.CompletedProcess(["ruff"], 1, json.dumps([FINDING]), ""),
            BROKEN_CONFIG,
        ],
        format_files=False,
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.issues).extracting("code").is_equal_to(["F401"])
    assert_that(result.output).contains("ruff check --fix printed no report")


def test_failed_check_with_a_report_stays_a_malformed_report(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
) -> None:
    """Exit 1 with a truncated JSON array is a parse failure, not a crash."""
    result = _fix(
        ruff_plugin,
        temp_python_file,
        [
            subprocess.CompletedProcess(["ruff"], 1, "[null,", ""),
            _clean("[]"),
        ],
        format_files=False,
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(str(result.output)).does_not_contain("Ruff could not run")


SYNTAX_ERROR = {
    "filename": "syn.py",
    "message": "Expected an identifier",
    "code": "invalid-syntax",
    "location": {"row": 1, "column": 5},
}
FORMAT_CHECK_SYNTAX_ERROR = (
    "invalid-syntax: Expected an identifier\n"
    " --> syn.py:1:5\n"
    "  |\n"
    "1 | def (:\n"
    "  |     ^\n"
)


def test_syntax_error_at_format_check_does_not_abort_the_run(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
) -> None:
    """``ruff format --check`` exiting 2 on invalid syntax is a normal outcome.

    With no other file to reformat it names no file, yet Ruff ran: the JSON
    check reports the same syntax error as a finding, and the lint fix still
    removes the unrelated F401.
    """
    report = [FINDING, SYNTAX_ERROR]
    result = _fix(
        ruff_plugin,
        temp_python_file,
        [
            subprocess.CompletedProcess(["ruff"], 1, json.dumps(report), ""),
            subprocess.CompletedProcess(["ruff"], 2, "", FORMAT_CHECK_SYNTAX_ERROR),
            subprocess.CompletedProcess(["ruff"], 1, json.dumps([SYNTAX_ERROR]), ""),
            subprocess.CompletedProcess(["ruff"], 1, json.dumps([SYNTAX_ERROR]), ""),
            subprocess.CompletedProcess(
                ["ruff"],
                2,
                "",
                "error: Failed to parse syn.py:1:5: Expected an identifier\n",
            ),
        ],
        format_files=True,
    )

    assert_that(result.execution_failed).is_false()
    assert_that(str(result.output)).does_not_contain("Ruff could not run")
    assert_that(result.initial_issues).extracting("code").contains("F401")
    assert_that(result.issues).extracting("message").is_equal_to(
        ["Expected an identifier"],
    )
    assert_that(result.fixed_issues_count).is_equal_to(1)
