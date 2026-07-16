"""Tests for OxfmtPlugin.fix method."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.oxfmt.oxfmt_issue import OxfmtIssue

if TYPE_CHECKING:
    from lintro.tools.definitions.oxfmt import OxfmtPlugin


def test_fix_success_no_issues(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix returns success when no issues to fix.

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
        # Initial check - no issues, fix - success, final check - no issues
        mock_run.side_effect = [
            (True, ""),
            (True, ""),
            (True, ""),
        ]

        result = oxfmt_plugin.fix(["/tmp/test.js"], {})

        assert_that(result.success).is_true()
        assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_success_with_fixes_applied(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix returns success when fixes are applied.

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
        mock_run.side_effect = [
            (False, "test.js"),  # Initial check - issue found
            (True, ""),  # Fix command
            (True, ""),  # Final check - no issues
        ]

        result = oxfmt_plugin.fix(["/tmp/test.js"], {})

        assert_that(result.success).is_true()
        assert_that(result.fixed_issues_count).is_equal_to(1)
        assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_timeout_during_initial_check(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix handles timeout during initial check.

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

        result = oxfmt_plugin.fix(["/tmp/test.js"], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")


def test_fix_timeout_during_fix_command(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix handles timeout during fix command.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    timeout_result = ToolResult(
        name="oxfmt",
        success=False,
        output="Oxfmt execution timed out (30s limit exceeded).",
        issues_count=2,
        issues=[
            OxfmtIssue(
                file="test.js",
                line=1,
                column=1,
                code="FORMAT",
                message="File is not formatted",
            ),
            OxfmtIssue(
                file="execution",
                line=1,
                column=1,
                code="TIMEOUT",
                message="Oxfmt execution timed out",
            ),
        ],
        initial_issues_count=2,
        fixed_issues_count=0,
        remaining_issues_count=2,
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
        mock_run.side_effect = [
            (False, "test.js"),  # Initial check - issue found
            subprocess.TimeoutExpired(cmd="oxfmt", timeout=30),  # Fix times out
        ]

        result = oxfmt_plugin.fix(["/tmp/test.js"], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")


def test_fix_early_return_when_should_skip(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix returns early result when should_skip is True.

    Args:
        oxfmt_plugin: The OxfmtPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    with patch.object(oxfmt_plugin, "_prepare_execution") as mock_prepare:
        ctx = mock_execution_context_for_tool(should_skip=True)
        ctx.early_result = MagicMock(success=True, issues_count=0)
        mock_prepare.return_value = ctx

        result = oxfmt_plugin.fix(["/tmp"], {})

        assert_that(result.success).is_true()


def test_fix_multiple_files_with_issues(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix handles multiple files with formatting issues.

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
            files=["test1.js", "test2.ts", "test3.tsx"],
            rel_files=["test1.js", "test2.ts", "test3.tsx"],
            cwd="/tmp",
        )

        mock_exec.return_value = ["oxfmt"]
        mock_config.return_value = []
        mock_run.side_effect = [
            (False, "test1.js\ntest2.ts\ntest3.tsx"),  # Initial check - 3 issues
            (True, ""),  # Fix command
            (True, ""),  # Final check - no issues
        ]

        result = oxfmt_plugin.fix(["/tmp"], {})

        assert_that(result.success).is_true()
        assert_that(result.initial_issues_count).is_equal_to(3)
        assert_that(result.fixed_issues_count).is_equal_to(3)
        assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_output_includes_fix_count(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix output includes count of fixed issues.

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
        mock_run.side_effect = [
            (False, "test.js"),  # Initial check - issue found
            (True, ""),  # Fix command
            (True, ""),  # Final check - no issues
        ]

        result = oxfmt_plugin.fix(["/tmp/test.js"], {})

        assert_that(result.output).contains("Fixed 1 formatting issue")
