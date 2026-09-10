"""Tests for Prettier output parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.tools.prettier.definition import PrettierPlugin


def test_check_parses_prettier_output_correctly(
    prettier_plugin: PrettierPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """Check correctly parses Prettier output with issues.

    Args:
        prettier_plugin: The prettier plugin instance to test.
        mock_execution_context_for_tool: Mock execution context factory.
    """
    with (
        patch.object(prettier_plugin, "_prepare_execution") as mock_prepare,
        patch.object(prettier_plugin, "_run_subprocess") as mock_run,
        patch.object(prettier_plugin, "_get_executable_command") as mock_exec,
        patch.object(prettier_plugin, "_build_config_args") as mock_config,
    ):
        mock_prepare.return_value = mock_execution_context_for_tool(
            files=["file1.js", "file2.js"],
            rel_files=["file1.js", "file2.js"],
            cwd="/tmp",
        )

        mock_exec.return_value = ["npx", "prettier"]
        mock_config.return_value = []
        mock_run.return_value = (
            False,
            "Checking formatting...\n[warn] file1.js\n[warn] file2.js\n"
            "[warn] Code style issues found in the above file(s).",
        )

        result = prettier_plugin.check(["/tmp"], {})

        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(2)
        assert_that(result.issues).is_not_none()
        assert_that(result.issues[0].file).is_equal_to("file1.js")  # type: ignore[index]  # validated via is_not_none
        assert_that(result.issues[1].file).is_equal_to("file2.js")  # type: ignore[index]  # validated via is_not_none
