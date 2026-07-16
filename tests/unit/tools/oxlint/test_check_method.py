"""Tests for OxlintPlugin.check method."""

from __future__ import annotations

import pathlib
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.parsers.oxlint.oxlint_issue import OxlintIssue

if TYPE_CHECKING:
    from lintro.tools.definitions.oxlint import OxlintPlugin


def test_check_with_issues(oxlint_plugin: OxlintPlugin, tmp_path: Path) -> None:
    """Check returns issues when found.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = pathlib.Path(tmp_path) / "test.js"
    test_file.write_text("var unused = 1;\n")

    mock_output = """{
        "diagnostics": [
            {
                "message": "Variable 'unused' is declared but never used.",
                "code": "eslint(no-unused-vars)",
                "severity": "warning",
                "filename": "test.js",
                "labels": [{"span": {"line": 1, "column": 5}}]
            }
        ]
    }"""
    mock_result = (False, mock_output)

    with (
        patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(oxlint_plugin, "_run_subprocess", return_value=mock_result),
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

        result = oxlint_plugin.check([str(test_file)], {})

        assert_that(result.name).is_equal_to("oxlint")
        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(1)
        assert_that(result.issues).is_not_none()
        issue = cast(OxlintIssue, result.issues[0])  # type: ignore[index]  # validated via is_not_none
        assert_that(issue.code).is_equal_to("eslint(no-unused-vars)")


def test_check_without_issues(oxlint_plugin: OxlintPlugin, tmp_path: Path) -> None:
    """Check returns success when no issues found.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = pathlib.Path(tmp_path) / "test.js"
    test_file.write_text("const x = 1;\nconsole.log(x);\n")

    mock_output = '{"diagnostics": []}'
    mock_result = (True, mock_output)

    with (
        patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(oxlint_plugin, "_run_subprocess", return_value=mock_result),
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

        result = oxlint_plugin.check([str(test_file)], {})

        assert_that(result.name).is_equal_to("oxlint")
        assert_that(result.success).is_true()
        assert_that(result.issues_count).is_equal_to(0)
        assert_that(result.output).is_none()


def test_check_timeout_handling(oxlint_plugin: OxlintPlugin, tmp_path: Path) -> None:
    """Check handles timeout correctly.

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

        result = oxlint_plugin.check([str(test_file)], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")
        assert_that(result.output).contains("30s")
        assert_that(result.issues_count).is_equal_to(1)
        issue = cast(OxlintIssue, result.issues[0])  # type: ignore[index]  # validated via is_not_none
        assert_that(issue.code).is_equal_to("TIMEOUT")


def test_check_early_skip(oxlint_plugin: OxlintPlugin, tmp_path: Path) -> None:
    """Check returns early when should_skip is True.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    from lintro.models.core.tool_result import ToolResult

    early_result = ToolResult(
        name="oxlint",
        success=True,
        output=None,
        issues_count=0,
        issues=[],
    )

    with patch.object(oxlint_plugin, "_prepare_execution") as mock_prepare:
        mock_ctx = MagicMock()
        mock_ctx.should_skip = True
        mock_ctx.early_result = early_result
        mock_prepare.return_value = mock_ctx

        result = oxlint_plugin.check([str(tmp_path)], {})

        assert_that(result).is_same_as(early_result)
