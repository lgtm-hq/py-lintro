"""Unit tests for Bandit clean-pass output not carrying informational text (#1534)."""

from __future__ import annotations

from pathlib import Path
from subprocess import (
    CompletedProcess,
)  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.bandit import BanditPlugin


def test_check_no_python_files_returns_empty_output(tmp_path: Path) -> None:
    """A bandit "no .py/.pyi files" result must not carry informational output.

    When bandit reports no Python files, the ToolResult must be a clean pass
    with empty ``output`` so the display layer never routes an informational
    string through the JSON parser (#1534).

    Args:
        tmp_path: Temporary directory path for the target file.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("x = 1\n")
    plugin = BanditPlugin()

    completed: CompletedProcess[str] = CompletedProcess(
        args=["bandit"],
        returncode=0,
        stdout="",
        stderr="No .py/.pyi files found to check.",
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch(
            "lintro.tools.definitions.bandit.subprocess.run",
            return_value=completed,
        ),
    ):
        result = plugin.check([str(py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()
    assert_that(result.parse_failures_count or 0).is_equal_to(0)


def test_check_empty_output_clean_pass_has_empty_output(tmp_path: Path) -> None:
    """An empty-stdout clean bandit run must not carry informational output.

    Args:
        tmp_path: Temporary directory path for the target file.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("x = 1\n")
    plugin = BanditPlugin()

    completed: CompletedProcess[str] = CompletedProcess(
        args=["bandit"],
        returncode=0,
        stdout="",
        stderr="",
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch(
            "lintro.tools.definitions.bandit.subprocess.run",
            return_value=completed,
        ),
    ):
        result = plugin.check([str(py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()
