"""Tests for OxfmtPlugin.check method."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.oxfmt.oxfmt_issue import OxfmtIssue

if TYPE_CHECKING:
    from lintro.tools.definitions.oxfmt import OxfmtPlugin


def test_check_returns_success_when_no_issues(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Check returns success when all files are formatted.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    with (
        patch.object(oxfmt_plugin, "_prepare_execution") as mock_prepare,
        patch.object(oxfmt_plugin, "_run_subprocess") as mock_run,
        patch.object(oxfmt_plugin, "_get_executable_command") as mock_exec,
        patch.object(oxfmt_plugin, "_build_config_args") as mock_config,
    ):
        mock_prepare.return_value = mock_execution_context_for_tool(
            files=["test.js"],
            rel_files=["test.js"],
            cwd="/tmp",
        )

        mock_exec.return_value = ["oxfmt"]
        mock_config.return_value = []
        mock_run.return_value = (True, "")

        result = oxfmt_plugin.check(["/tmp/test.js"], {})

        assert_that(result.success).is_true()
        assert_that(result.issues_count).is_equal_to(0)


def test_check_returns_issues_when_unformatted_files(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Check returns issues when files need formatting.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    with (
        patch.object(oxfmt_plugin, "_prepare_execution") as mock_prepare,
        patch.object(oxfmt_plugin, "_run_subprocess") as mock_run,
        patch.object(oxfmt_plugin, "_get_executable_command") as mock_exec,
        patch.object(oxfmt_plugin, "_build_config_args") as mock_config,
    ):
        mock_prepare.return_value = mock_execution_context_for_tool(
            files=["test.js", "other.ts"],
            rel_files=["test.js", "other.ts"],
            cwd="/tmp",
        )

        mock_exec.return_value = ["oxfmt"]
        mock_config.return_value = []
        mock_run.return_value = (False, "test.js\nother.ts")

        result = oxfmt_plugin.check(["/tmp/test.js", "/tmp/other.ts"], {})

        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(2)
        assert_that(result.issues).is_not_none()
        issues = result.issues
        assert issues is not None  # narrow type for mypy
        assert_that(issues[0].file).is_equal_to("test.js")
        assert_that(issues[1].file).is_equal_to("other.ts")


def test_check_timeout_handling(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Check handles timeout correctly.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    timeout_result = ToolResult(
        name="oxfmt",
        success=False,
        output="Oxfmt execution timed out (30s limit exceeded).",
        issues_count=1,
        issues=[
            OxfmtIssue(
                file="execution",
                line=1,
                column=1,
                code="TIMEOUT",
                message="Oxfmt execution timed out",
            ),
        ],
        initial_issues_count=1,
        fixed_issues_count=0,
        remaining_issues_count=1,
    )

    with (
        patch.object(oxfmt_plugin, "_prepare_execution") as mock_prepare,
        patch.object(oxfmt_plugin, "_run_subprocess") as mock_run,
        patch.object(oxfmt_plugin, "_get_executable_command") as mock_exec,
        patch.object(oxfmt_plugin, "_build_config_args") as mock_config,
        patch.object(
            oxfmt_plugin,
            "_create_timeout_result",
            return_value=timeout_result,
        ),
    ):
        mock_prepare.return_value = mock_execution_context_for_tool(
            files=["test.js"],
            rel_files=["test.js"],
            cwd="/tmp",
        )

        mock_exec.return_value = ["oxfmt"]
        mock_config.return_value = []
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="oxfmt", timeout=30)

        result = oxfmt_plugin.check(["/tmp/test.js"], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")


def test_check_early_return_when_should_skip(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Check returns early result when should_skip is True.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    with patch.object(oxfmt_plugin, "_prepare_execution") as mock_prepare:
        ctx = mock_execution_context_for_tool(should_skip=True)
        ctx.early_result = MagicMock(success=True, issues_count=0)
        mock_prepare.return_value = ctx

        result = oxfmt_plugin.check(["/tmp"], {})

        assert_that(result.success).is_true()


def test_check_suppresses_output_on_success(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Check returns None output when no issues found.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    with (
        patch.object(oxfmt_plugin, "_prepare_execution") as mock_prepare,
        patch.object(oxfmt_plugin, "_run_subprocess") as mock_run,
        patch.object(oxfmt_plugin, "_get_executable_command") as mock_exec,
        patch.object(oxfmt_plugin, "_build_config_args") as mock_config,
    ):
        mock_prepare.return_value = mock_execution_context_for_tool(
            files=["test.js"],
            rel_files=["test.js"],
            cwd="/tmp",
        )

        mock_exec.return_value = ["oxfmt"]
        mock_config.return_value = []
        mock_run.return_value = (True, "")

        result = oxfmt_plugin.check(["/tmp/test.js"], {})

        assert_that(result.output).is_none()
