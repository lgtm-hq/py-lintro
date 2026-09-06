"""Tests for execute_ruff_fix with real RuffPlugin."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.ruff.fix import execute_ruff_fix

if TYPE_CHECKING:
    from lintro.tools.ruff.definition import RuffPlugin


def test_execute_ruff_fix_with_real_plugin_no_files(
    ruff_plugin: RuffPlugin,
) -> None:
    """Return success when paths are empty using real plugin.

    Args:
        ruff_plugin: RuffPlugin instance for testing.
    """
    result = execute_ruff_fix(ruff_plugin, [])

    assert_that(result.success).is_true()
    assert_that(result.output).is_equal_to("No files to fix.")


def test_execute_ruff_fix_with_real_plugin_nonexistent_path(
    ruff_plugin: RuffPlugin,
) -> None:
    """Raise FileNotFoundError for nonexistent paths using real plugin.

    Args:
        ruff_plugin: RuffPlugin instance for testing.
    """
    with pytest.raises(FileNotFoundError):
        execute_ruff_fix(ruff_plugin, ["/nonexistent/path"])


def test_execute_ruff_fix_integration_with_temp_file(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
) -> None:
    """Execute fix on actual temp file using real plugin.

    Args:
        ruff_plugin: RuffPlugin instance for testing.
        temp_python_file: Temporary Python file for testing.
    """
    with (
        patch.object(ruff_plugin, "_run_subprocess") as mock_run,
        patch.object(
            ruff_plugin,
            "_verify_tool_version",
        ) as mock_verify,
    ):
        mock_verify.return_value = None
        mock_run.return_value = (True, "[]")

        result = execute_ruff_fix(ruff_plugin, [temp_python_file])

    assert_that(result.success).is_true()
