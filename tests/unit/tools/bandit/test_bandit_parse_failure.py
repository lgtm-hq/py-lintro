"""Unit tests for Bandit fail-closed parsing on unparseable output (#1044)."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import (
    CompletedProcess,
)  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.tools.bandit.definition import BanditPlugin


@pytest.mark.parametrize("returncode", [0, 1], ids=["rc0", "rc1"])
@pytest.mark.parametrize(
    "results_fields",
    [
        {},
        {"results": None},
        {"results": {}},
        {"results": ""},
        {"results": "invalid"},
        {"results": 0},
        {"results": False},
    ],
    ids=["missing", "null", "object", "empty-string", "string", "number", "boolean"],
)
def test_check_invalid_results_fails_closed(
    tmp_path: Path,
    results_fields: dict[str, object],
    returncode: int,
) -> None:
    """Malformed results must count as parse failures at either exit code.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
        results_fields: Missing or non-list primary results container.
        returncode: Simulated Bandit process exit code.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("x = 1\n")
    plugin = BanditPlugin()
    stdout = json.dumps({"errors": [], **results_fields})
    completed = CompletedProcess(
        args=["bandit"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
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
        result = plugin.check([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.outcome).is_equal_to(
        ToolOutcome(execution=Checked(success=False)),
    )
    assert_that(result.issues).is_empty()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).is_equal_to(stdout)


def test_check_garbage_stdout_with_zero_exit_is_not_clean(tmp_path: Path) -> None:
    """Unparseable bandit stdout with a zero exit must not report a clean pass.

    Args:
        tmp_path: Temporary directory path for the target Python file.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("x = 1\n")
    plugin = BanditPlugin()

    completed: CompletedProcess[str] = CompletedProcess(
        args=["bandit"],
        returncode=0,
        stdout="this is not valid json at all",
        stderr="",
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
        result = plugin.check([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_valid_json_reports_no_parse_failures(tmp_path: Path) -> None:
    """A clean bandit run reports zero parse failures.

    Args:
        tmp_path: Temporary directory path for the target Python file.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("x = 1\n")
    plugin = BanditPlugin()

    completed: CompletedProcess[str] = CompletedProcess(
        args=["bandit"],
        returncode=0,
        stdout='{"results": [], "errors": []}',
        stderr="",
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
        result = plugin.check([str(py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(0)
