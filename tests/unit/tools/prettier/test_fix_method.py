"""Tests for PrettierPlugin.fix method."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.tools.definitions.prettier import PrettierPlugin


def test_fix_success_no_issues(
    prettier_plugin: PrettierPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix returns success when no issues to fix.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    with (
        patch.object(prettier_plugin, "_prepare_execution") as mock_prepare,
        patch.object(prettier_plugin, "_run_subprocess") as mock_run,
        patch.object(prettier_plugin, "_get_executable_command") as mock_exec,
        patch.object(prettier_plugin, "_build_config_args") as mock_config,
    ):
        mock_prepare.return_value = mock_execution_context_for_tool(
            files=["test.js"],
            rel_files=["test.js"],
            cwd="/tmp",
        )

        mock_exec.return_value = ["npx", "prettier"]
        mock_config.return_value = []
        mock_run.return_value = (True, "All matched files use Prettier code style!")

        result = prettier_plugin.fix(["/tmp/test.js"], {})

        assert_that(result.success).is_true()
        assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_success_with_fixes_applied(
    prettier_plugin: PrettierPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix returns success when fixes applied.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    with (
        patch.object(prettier_plugin, "_prepare_execution") as mock_prepare,
        patch.object(prettier_plugin, "_run_subprocess") as mock_run,
        patch.object(prettier_plugin, "_get_executable_command") as mock_exec,
        patch.object(prettier_plugin, "_build_config_args") as mock_config,
    ):
        mock_prepare.return_value = mock_execution_context_for_tool(
            files=["test.js"],
            rel_files=["test.js"],
            cwd="/tmp",
        )

        mock_exec.return_value = ["npx", "prettier"]
        mock_config.return_value = []
        mock_run.side_effect = [
            (False, "[warn] test.js\n[warn] Code style issues found."),
            (True, "test.js"),
            (True, "All matched files use Prettier code style!"),
        ]

        result = prettier_plugin.fix(["/tmp/test.js"], {})

        assert_that(result.success).is_true()
        assert_that(result.fixed_issues_count).is_greater_than_or_equal_to(1)


def test_fix_timeout_during_check(
    prettier_plugin: PrettierPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix handles timeout during initial check.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.prettier.prettier_issue import PrettierIssue

    timeout_result = ToolResult(
        name="prettier",
        success=False,
        output="Prettier execution timed out (30s limit exceeded).",
        issues_count=1,
        issues=[
            PrettierIssue(
                file="execution",
                line=1,
                column=1,
                code="TIMEOUT",
                message="Prettier execution timed out",
            ),
        ],
        initial_issues_count=1,
        fixed_issues_count=0,
        remaining_issues_count=1,
    )

    with (
        patch.object(prettier_plugin, "_prepare_execution") as mock_prepare,
        patch.object(prettier_plugin, "_run_subprocess") as mock_run,
        patch.object(prettier_plugin, "_get_executable_command") as mock_exec,
        patch.object(prettier_plugin, "_build_config_args") as mock_config,
        patch.object(
            prettier_plugin,
            "_create_timeout_result",
            return_value=timeout_result,
        ),
    ):
        mock_prepare.return_value = mock_execution_context_for_tool(
            files=["test.js"],
            rel_files=["test.js"],
            cwd="/tmp",
        )

        mock_exec.return_value = ["npx", "prettier"]
        mock_config.return_value = []
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="prettier", timeout=30)

        result = prettier_plugin.fix(["/tmp/test.js"], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("timed out")


def test_fix_early_return_when_should_skip(
    prettier_plugin: PrettierPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Fix returns early result when should_skip is True.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    with patch.object(prettier_plugin, "_prepare_execution") as mock_prepare:
        ctx = mock_execution_context_for_tool(should_skip=True)
        ctx.early_result = MagicMock(success=True, issues_count=0)
        mock_prepare.return_value = ctx

        result = prettier_plugin.fix(["/tmp"], {})

        assert_that(result.success).is_true()
