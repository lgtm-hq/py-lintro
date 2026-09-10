"""Tests with real RuffPlugin instance."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.tools.ruff.check import execute_ruff_check

if TYPE_CHECKING:
    from lintro.tools.ruff.definition import RuffPlugin


def test_execute_ruff_check_with_real_plugin_no_files(
    ruff_plugin: RuffPlugin,
) -> None:
    """Execute with real plugin when no files to check.

    Args:
        ruff_plugin: RuffPlugin instance for testing.
    """
    with patch.object(ruff_plugin, "_verify_tool_version", return_value=None):
        result = execute_ruff_check(ruff_plugin, [])

        assert_that(result.success).is_true()
        assert_that(result.output).is_equal_to("No files to check.")


def test_execute_ruff_check_with_real_plugin(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
) -> None:
    """Execute with real plugin and temp file.

    Args:
        ruff_plugin: RuffPlugin instance for testing.
        temp_python_file: Temporary Python file for testing.
    """
    with (
        patch.object(ruff_plugin, "_verify_tool_version", return_value=None),
        patch(
            "lintro.tools.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
    ):
        result = execute_ruff_check(ruff_plugin, [temp_python_file])

        assert_that(result.success).is_true()
        assert_that(result.name).is_equal_to(ToolName.RUFF)
