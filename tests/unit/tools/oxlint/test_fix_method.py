"""Tests for OxlintPlugin.fix method."""

from __future__ import annotations

import pathlib
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.parsers.oxlint.oxlint_issue import OxlintIssue

if TYPE_CHECKING:
    from lintro.tools.definitions.oxlint import OxlintPlugin


def test_fix_success_all_fixed(oxlint_plugin: OxlintPlugin, tmp_path: Path) -> None:
    """Fix returns success when all issues fixed.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = pathlib.Path(tmp_path) / "test.js"
    test_file.write_text("var x = 1;\n")

    initial_output = """{
        "diagnostics": [
            {
                "message": "Use 'const' instead of 'var'.",
                "code": "eslint(prefer-const)",
                "severity": "warning",
                "filename": "test.js",
                "labels": [{"span": {"line": 1, "column": 1}}]
            }
        ]
    }"""
    final_output = '{"diagnostics": []}'

    call_count = 0

    def mock_run_subprocess(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (False, initial_output)
        elif call_count == 2:
            return (True, "")
        else:
            return (True, final_output)

    with (
        patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            oxlint_plugin,
            "_run_subprocess",
            side_effect=mock_run_subprocess,
        ),
        patch.object(oxlint_plugin, "_get_executable_command", return_value=["oxlint"]),
        patch.object(oxlint_plugin, "_build_config_args", return_value=[]),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.rel_files = ["test.js"]
        mock_ctx.files = [str(test_file)]
        mock_prepare.return_value = mock_ctx

        result = oxlint_plugin.fix([str(test_file)], {})

        assert_that(result.name).is_equal_to("oxlint")
        assert_that(result.success).is_true()
        assert_that(result.initial_issues_count).is_equal_to(1)
        assert_that(result.fixed_issues_count).is_equal_to(1)
        assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_partial_fix(oxlint_plugin: OxlintPlugin, tmp_path: Path) -> None:
    """Fix returns remaining issues when not all can be fixed.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = pathlib.Path(tmp_path) / "test.js"
    test_file.write_text("var x = 1;\n")

    initial_output = """{
        "diagnostics": [
            {
                "message": "Use 'const' instead of 'var'.",
                "code": "eslint(prefer-const)",
                "severity": "warning",
                "filename": "test.js",
                "labels": [{"span": {"line": 1, "column": 1}}]
            },
            {
                "message": "Unused variable x.",
                "code": "eslint(no-unused-vars)",
                "severity": "warning",
                "filename": "test.js",
                "labels": [{"span": {"line": 1, "column": 5}}]
            }
        ]
    }"""
    final_output = """{
        "diagnostics": [
            {
                "message": "Unused variable x.",
                "code": "eslint(no-unused-vars)",
                "severity": "warning",
                "filename": "test.js",
                "labels": [{"span": {"line": 1, "column": 7}}]
            }
        ]
    }"""

    call_count = 0

    def mock_run_subprocess(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (False, initial_output)
        elif call_count == 2:
            return (True, "")
        else:
            return (False, final_output)

    with (
        patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            oxlint_plugin,
            "_run_subprocess",
            side_effect=mock_run_subprocess,
        ),
        patch.object(oxlint_plugin, "_get_executable_command", return_value=["oxlint"]),
        patch.object(oxlint_plugin, "_build_config_args", return_value=[]),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.rel_files = ["test.js"]
        mock_ctx.files = [str(test_file)]
        mock_prepare.return_value = mock_ctx

        result = oxlint_plugin.fix([str(test_file)], {})

        assert_that(result.success).is_false()
        assert_that(result.initial_issues_count).is_equal_to(2)
        assert_that(result.fixed_issues_count).is_equal_to(1)
        assert_that(result.remaining_issues_count).is_equal_to(1)


def test_fix_timeout_on_initial_check(
    oxlint_plugin: OxlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout on initial check.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = pathlib.Path(tmp_path) / "test.js"
    test_file.write_text("const x = 1;\n")

    with (
        patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            oxlint_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["oxlint"], timeout=30),
        ),
        patch.object(oxlint_plugin, "_get_executable_command", return_value=["oxlint"]),
        patch.object(oxlint_plugin, "_build_config_args", return_value=[]),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.rel_files = ["test.js"]
        mock_ctx.files = [str(test_file)]
        mock_prepare.return_value = mock_ctx

        result = oxlint_plugin.fix([str(test_file)], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")


def test_fix_timeout_on_fix_command(
    oxlint_plugin: OxlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout on fix command.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    from lintro.models.core.tool_result import ToolResult

    test_file = pathlib.Path(tmp_path) / "test.js"
    test_file.write_text("var x = 1;\n")

    initial_output = """{
        "diagnostics": [
            {
                "message": "Use 'const' instead of 'var'.",
                "code": "eslint(prefer-const)",
                "severity": "warning",
                "filename": "test.js",
                "labels": [{"span": {"line": 1, "column": 1}}]
            }
        ]
    }"""

    timeout_result = ToolResult(
        name="oxlint",
        success=False,
        output="Oxlint execution timed out (30s limit exceeded).",
        issues_count=1,
        issues=[
            OxlintIssue(
                file="execution",
                line=1,
                column=1,
                code="TIMEOUT",
                message="Oxlint execution timed out",
                severity="error",
                fixable=False,
            ),
        ],
        initial_issues_count=1,
        fixed_issues_count=0,
        remaining_issues_count=1,
    )

    call_count = 0

    def mock_run_subprocess(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (False, initial_output)
        else:
            raise subprocess.TimeoutExpired(cmd=["oxlint"], timeout=30)

    with (
        patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            oxlint_plugin,
            "_run_subprocess",
            side_effect=mock_run_subprocess,
        ),
        patch.object(oxlint_plugin, "_get_executable_command", return_value=["oxlint"]),
        patch.object(oxlint_plugin, "_build_config_args", return_value=[]),
        patch.object(
            oxlint_plugin,
            "_create_timeout_result",
            return_value=timeout_result,
        ),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.rel_files = ["test.js"]
        mock_ctx.files = [str(test_file)]
        mock_prepare.return_value = mock_ctx

        result = oxlint_plugin.fix([str(test_file)], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")


def test_fix_timeout_on_final_check(
    oxlint_plugin: OxlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout on final check.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    from lintro.models.core.tool_result import ToolResult

    test_file = pathlib.Path(tmp_path) / "test.js"
    test_file.write_text("var x = 1;\n")

    initial_output = """{
        "diagnostics": [
            {
                "message": "Use 'const' instead of 'var'.",
                "code": "eslint(prefer-const)",
                "severity": "warning",
                "filename": "test.js",
                "labels": [{"span": {"line": 1, "column": 1}}]
            }
        ]
    }"""

    timeout_result = ToolResult(
        name="oxlint",
        success=False,
        output="Oxlint execution timed out (30s limit exceeded).",
        issues_count=1,
        issues=[
            OxlintIssue(
                file="execution",
                line=1,
                column=1,
                code="TIMEOUT",
                message="Oxlint execution timed out",
                severity="error",
                fixable=False,
            ),
        ],
        initial_issues_count=1,
        fixed_issues_count=0,
        remaining_issues_count=1,
    )

    call_count = 0

    def mock_run_subprocess(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (False, initial_output)
        elif call_count == 2:
            return (True, "")
        else:
            raise subprocess.TimeoutExpired(cmd=["oxlint"], timeout=30)

    with (
        patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            oxlint_plugin,
            "_run_subprocess",
            side_effect=mock_run_subprocess,
        ),
        patch.object(oxlint_plugin, "_get_executable_command", return_value=["oxlint"]),
        patch.object(oxlint_plugin, "_build_config_args", return_value=[]),
        patch.object(
            oxlint_plugin,
            "_create_timeout_result",
            return_value=timeout_result,
        ),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.rel_files = ["test.js"]
        mock_ctx.files = [str(test_file)]
        mock_prepare.return_value = mock_ctx

        result = oxlint_plugin.fix([str(test_file)], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")


def test_fix_early_skip(oxlint_plugin: OxlintPlugin, tmp_path: Path) -> None:
    """Fix returns early when should_skip is True.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    from lintro.models.core.tool_result import ToolResult

    early_result = ToolResult(
        name="oxlint",
        success=True,
        output="No files to fix.",
        issues_count=0,
        issues=[],
    )

    with patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare:
        mock_ctx = MagicMock()
        mock_ctx.should_skip = True
        mock_ctx.early_result = early_result
        mock_prepare.return_value = mock_ctx

        result = oxlint_plugin.fix([str(tmp_path)], {})

        assert_that(result).is_same_as(early_result)


def test_fix_unfixable_issues(oxlint_plugin: OxlintPlugin, tmp_path: Path) -> None:
    """Fix reports unfixable issues correctly.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = pathlib.Path(tmp_path) / "test.js"
    test_file.write_text("const x = 1;\n")

    # No fixable issues - all issues remain after fix
    initial_output = """{
        "diagnostics": [
            {
                "message": "Unused variable x.",
                "code": "eslint(no-unused-vars)",
                "severity": "warning",
                "filename": "test.js",
                "labels": [{"span": {"line": 1, "column": 7}}]
            }
        ]
    }"""
    final_output = initial_output  # Same issues remain

    call_count = 0

    def mock_run_subprocess(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (False, initial_output)
        elif call_count == 2:
            return (True, "")
        else:
            return (False, final_output)

    with (
        patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            oxlint_plugin,
            "_run_subprocess",
            side_effect=mock_run_subprocess,
        ),
        patch.object(oxlint_plugin, "_get_executable_command", return_value=["oxlint"]),
        patch.object(oxlint_plugin, "_build_config_args", return_value=[]),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.rel_files = ["test.js"]
        mock_ctx.files = [str(test_file)]
        mock_prepare.return_value = mock_ctx

        result = oxlint_plugin.fix([str(test_file)], {})

        assert_that(result.success).is_false()
        assert_that(result.initial_issues_count).is_equal_to(1)
        assert_that(result.fixed_issues_count).is_equal_to(0)
        assert_that(result.remaining_issues_count).is_equal_to(1)
        assert_that(result.output).contains("cannot be auto-fixed")


def test_fix_no_issues(oxlint_plugin: OxlintPlugin, tmp_path: Path) -> None:
    """Fix returns success when no issues found.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = pathlib.Path(tmp_path) / "test.js"
    test_file.write_text("const x = 1;\nconsole.log(x);\n")

    mock_output = '{"diagnostics": []}'

    call_count = 0

    def mock_run_subprocess(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return (True, mock_output)

    with (
        patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            oxlint_plugin,
            "_run_subprocess",
            side_effect=mock_run_subprocess,
        ),
        patch.object(oxlint_plugin, "_get_executable_command", return_value=["oxlint"]),
        patch.object(oxlint_plugin, "_build_config_args", return_value=[]),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.rel_files = ["test.js"]
        mock_ctx.files = [str(test_file)]
        mock_prepare.return_value = mock_ctx

        result = oxlint_plugin.fix([str(test_file)], {})

        assert_that(result.success).is_true()
        assert_that(result.initial_issues_count).is_equal_to(0)
        assert_that(result.fixed_issues_count).is_equal_to(0)
        assert_that(result.remaining_issues_count).is_equal_to(0)
